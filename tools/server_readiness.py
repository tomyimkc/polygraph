#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
"""Production-readiness evidence harness for an OpenAI-adjacent llama-server.

This is deliberately a *readiness evidence* tool, not a production-ready claim
generator.  It starts one pinned local llama-server, drives a mixed concurrent
workload, retains every request and one-second host/process telemetry sample,
tests controlled restart recovery, evaluates explicit SLOs, and emits a
content-addressed evidence directory.

The harness is stdlib-only.  It is intended to run on Arm64 CI or owned Arm
hardware, but records the observed architecture rather than assuming it.

Exit codes:
  0  every configured readiness gate passed
  1  measurement completed but one or more gates failed
  2  infrastructure prevented a valid measurement

Even a gate PASS is not actual-production evidence: the output always carries
``actualProductionTraffic:false`` and ``productionReady:false``.  Live traffic,
multi-day availability, failover, and application correctness remain separate
evidence requirements.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import http.client
import itertools
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "polygraph.server-readiness.v1"
REQUEST_SCHEMA = "polygraph.server-readiness.request.v1"

PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "short-chat",
        "Reply with one concise sentence: why is executed-kernel evidence "
        "stronger than a startup banner?",
    ),
    (
        "short-chat",
        "In one sentence, distinguish an optimization claim from a verified "
        "runtime observation.",
    ),
    (
        "rag-like",
        "Use only this context: Polygraph separates static symbols, runtime "
        "selection logs, and executed dispatch counts. Question: which layer "
        "directly establishes that machine code ran? Answer briefly.",
    ),
    (
        "rag-like",
        "Context: a build exits zero, prints KLEIDIAI=1, but contains no "
        "kai_run_matmul symbols. State the operational risk in two sentences.",
    ),
    (
        "long-summary",
        "Summarize this incident in four short bullet points: a documented "
        "Arm build completed successfully; the capability banner advertised "
        "KleidiAI; symbol inspection found no accelerated matmul kernels; an "
        "explicit Arm architecture flag restored the kernels; debugger "
        "dispatch counting then confirmed which family executed under load. "
        "Keep the summary factual and avoid claiming universal applicability.",
    ),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("percentile q must be in [0, 100]")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (q / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


@dataclass
class RequestResult:
    schema: str
    phase: str
    concurrency: int
    workload: str
    startedAtUtc: str
    status: str
    httpStatus: int | None
    ttftMs: float | None
    e2eMs: float
    promptTokens: int | None
    completionTokens: int | None
    outputCharacters: int
    error: str | None


def _extract_usage(obj: dict[str, Any]) -> tuple[int | None, int | None]:
    timings = obj.get("timings")
    if not isinstance(timings, dict):
        timings = {}
    prompt = obj.get("tokens_evaluated", timings.get("prompt_n"))
    completion = obj.get("tokens_predicted", timings.get("predicted_n"))
    try:
        prompt_i = int(prompt) if prompt is not None else None
    except (TypeError, ValueError):
        prompt_i = None
    try:
        completion_i = int(completion) if completion is not None else None
    except (TypeError, ValueError):
        completion_i = None
    return prompt_i, completion_i


def stream_completion(
    host: str,
    port: int,
    prompt: str,
    max_tokens: int,
    timeout: float,
    *,
    phase: str,
    concurrency: int,
    workload: str,
) -> RequestResult:
    started_wall = utc_now()
    started = time.monotonic()
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    body = json.dumps(
        {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0,
            "seed": 1,
            "stream": True,
            "cache_prompt": False,
        },
        separators=(",", ":"),
    )
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    first_content_at: float | None = None
    output: list[str] = []
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    status_code: int | None = None
    error: str | None = None

    try:
        conn.request("POST", "/completion", body=body, headers=headers)
        response = conn.getresponse()
        status_code = response.status
        if status_code != 200:
            payload = response.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {status_code}: {payload[:500]}")

        content_type = response.getheader("Content-Type", "")
        if "text/event-stream" in content_type:
            while True:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = obj.get("content")
                if isinstance(text, str) and text:
                    if first_content_at is None:
                        first_content_at = time.monotonic()
                    output.append(text)
                p, c = _extract_usage(obj)
                if p is not None:
                    prompt_tokens = p
                if c is not None:
                    completion_tokens = c
        else:
            payload = response.read()
            obj = json.loads(payload)
            text = obj.get("content")
            if isinstance(text, str) and text:
                first_content_at = time.monotonic()
                output.append(text)
            prompt_tokens, completion_tokens = _extract_usage(obj)
    except Exception as exc:  # request evidence must retain every failure
        error = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()

    ended = time.monotonic()
    combined = "".join(output)
    success = (
        error is None
        and status_code == 200
        and bool(combined.strip())
        and completion_tokens is not None
        and completion_tokens > 0
    )
    return RequestResult(
        schema=REQUEST_SCHEMA,
        phase=phase,
        concurrency=concurrency,
        workload=workload,
        startedAtUtc=started_wall,
        status="success" if success else "error",
        httpStatus=status_code,
        ttftMs=(
            round((first_content_at - started) * 1000.0, 3)
            if first_content_at is not None
            else None
        ),
        e2eMs=round((ended - started) * 1000.0, 3),
        promptTokens=prompt_tokens,
        completionTokens=completion_tokens,
        outputCharacters=len(combined),
        error=error if error else (None if success else "incomplete-or-empty-response"),
    )


def _health(host: str, port: int, timeout: float = 2.0) -> bool:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        response.read(4096)
        return response.status == 200
    except OSError:
        return False
    finally:
        conn.close()


def wait_ready(
    process: subprocess.Popen[Any],
    host: str,
    port: int,
    timeout: float,
) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        rc = process.poll()
        if rc is not None:
            raise RuntimeError(f"llama-server exited before readiness (rc={rc})")
        if _health(host, port):
            return time.monotonic() - start
        time.sleep(0.5)
    raise TimeoutError(f"llama-server did not become ready within {timeout:.1f}s")


def _read_status_value(pid: int, key: str) -> str | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(f"{key}:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _rss_mib(pid: int) -> float | None:
    value = _read_status_value(pid, "VmRSS")
    if not value:
        return None
    try:
        kib = float(value.split()[0])
        return kib / 1024.0
    except (ValueError, IndexError):
        return None


def _thread_count(pid: int) -> int | None:
    value = _read_status_value(pid, "Threads")
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _max_temperature_c() -> float | None:
    values: list[float] = []
    root = Path("/sys/class/thermal")
    if not root.exists():
        return None
    for path in root.glob("thermal_zone*/temp"):
        try:
            raw = float(path.read_text().strip())
        except (OSError, ValueError):
            continue
        values.append(raw / 1000.0 if raw > 1000 else raw)
    return max(values) if values else None


class TelemetrySampler:
    def __init__(self, process: subprocess.Popen[Any], phase: str) -> None:
        self.process = process
        self.phase = phase
        self.rows: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("telemetry sampler did not stop within 5 seconds")
        return list(self.rows)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                load1 = os.getloadavg()[0]
            except (AttributeError, OSError):
                load1 = None
            self.rows.append(
                {
                    "timestampUtc": utc_now(),
                    "phase": self.phase,
                    "pid": self.process.pid,
                    "serverAlive": self.process.poll() is None,
                    "rssMiB": _rss_mib(self.process.pid),
                    "threads": _thread_count(self.process.pid),
                    "load1": load1,
                    "temperatureMaxC": _max_temperature_c(),
                }
            )
            self._stop.wait(1.0)


def summarize_results(
    phase: str,
    concurrency: int,
    results: Sequence[RequestResult],
    duration_seconds: float,
) -> dict[str, Any]:
    successes = [r for r in results if r.status == "success"]
    ttft = [r.ttftMs for r in successes if r.ttftMs is not None]
    e2e = [r.e2eMs for r in successes]
    completion_tokens = sum(r.completionTokens or 0 for r in successes)
    prompt_tokens = sum(r.promptTokens or 0 for r in successes)
    return {
        "phase": phase,
        "concurrency": concurrency,
        "durationSeconds": round(duration_seconds, 3),
        "requests": len(results),
        "successes": len(successes),
        "errors": len(results) - len(successes),
        "errorRate": (
            (len(results) - len(successes)) / len(results) if results else 1.0
        ),
        "requestsPerSecond": (
            len(results) / duration_seconds if duration_seconds > 0 else 0.0
        ),
        "outputTokensPerSecond": (
            completion_tokens / duration_seconds if duration_seconds > 0 else 0.0
        ),
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "usageMissingRequests": sum(
            1
            for r in results
            if r.promptTokens is None or r.completionTokens is None
        ),
        "emptyOutputRequests": sum(1 for r in results if r.outputCharacters <= 0),
        "ttftP50Ms": percentile(ttft, 50),
        "ttftP95Ms": percentile(ttft, 95),
        "ttftP99Ms": percentile(ttft, 99),
        "e2eP50Ms": percentile(e2e, 50),
        "e2eP95Ms": percentile(e2e, 95),
        "e2eP99Ms": percentile(e2e, 99),
    }


def evaluate_gate(
    *,
    machine: str,
    summaries: Sequence[dict[str, Any]],
    restarts: Sequence[dict[str, Any]],
    telemetry: Sequence[dict[str, Any]],
    min_requests: int,
    max_error_rate: float,
    max_ttft_p99_ms: float,
    max_e2e_p99_ms: float,
    max_recovery_seconds: float,
    max_rss_mib: float,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, observed: Any, limit: Any) -> None:
        checks.append(
            {"name": name, "passed": bool(passed), "observed": observed, "limit": limit}
        )

    arm = machine.lower() in {"aarch64", "arm64"}
    add("arm64-architecture", arm, machine, "aarch64 or arm64")

    total_requests = sum(int(s["requests"]) for s in summaries)
    add("minimum-measured-requests", total_requests >= min_requests, total_requests, min_requests)

    worst_error = max((float(s["errorRate"]) for s in summaries), default=1.0)
    add("error-rate", worst_error <= max_error_rate, worst_error, max_error_rate)

    usage_missing = sum(int(s["usageMissingRequests"]) for s in summaries)
    add("usage-accounting", usage_missing == 0, usage_missing, 0)

    empty_outputs = sum(int(s["emptyOutputRequests"]) for s in summaries)
    add("non-empty-output", empty_outputs == 0, empty_outputs, 0)

    soak = next((s for s in summaries if s["phase"] == "soak"), None)
    soak_ttft = soak.get("ttftP99Ms") if soak else None
    soak_e2e = soak.get("e2eP99Ms") if soak else None
    add(
        "soak-ttft-p99",
        soak_ttft is not None and float(soak_ttft) <= max_ttft_p99_ms,
        soak_ttft,
        max_ttft_p99_ms,
    )
    add(
        "soak-e2e-p99",
        soak_e2e is not None and float(soak_e2e) <= max_e2e_p99_ms,
        soak_e2e,
        max_e2e_p99_ms,
    )

    restart_failures = [r for r in restarts if not r.get("success")]
    max_recovery = max(
        (float(r["readySeconds"]) for r in restarts if r.get("readySeconds") is not None),
        default=None,
    )
    add("restart-success", not restart_failures and bool(restarts), len(restart_failures), 0)
    add(
        "restart-recovery",
        max_recovery is not None and max_recovery <= max_recovery_seconds,
        max_recovery,
        max_recovery_seconds,
    )

    rss_values = [
        float(r["rssMiB"]) for r in telemetry if r.get("rssMiB") is not None
    ]
    rss_max = max(rss_values) if rss_values else None
    add(
        "server-rss",
        rss_max is not None and rss_max <= max_rss_mib,
        rss_max,
        max_rss_mib,
    )

    return {
        "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL",
        "checks": checks,
    }


def run_phase(
    *,
    host: str,
    port: int,
    phase: str,
    concurrency: int,
    duration_seconds: float,
    warmup_requests: int,
    max_tokens: int,
    request_timeout: float,
) -> tuple[list[RequestResult], list[RequestResult], float]:
    prompt_counter = itertools.count()
    prompt_lock = threading.Lock()

    def next_prompt() -> tuple[str, str]:
        with prompt_lock:
            workload, prompt = PROMPTS[next(prompt_counter) % len(PROMPTS)]
        return workload, prompt

    def one_request(target_phase: str) -> RequestResult:
        workload, prompt = next_prompt()
        return stream_completion(
            host,
            port,
            prompt,
            max_tokens,
            request_timeout,
            phase=target_phase,
            concurrency=concurrency,
            workload=workload,
        )

    warmups: list[RequestResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, f"{phase}-warmup") for _ in range(warmup_requests)]
        for future in futures:
            warmups.append(future.result())

    measured: list[RequestResult] = []
    start = time.monotonic()
    deadline = start + duration_seconds

    def worker() -> list[RequestResult]:
        rows: list[RequestResult] = []
        while time.monotonic() < deadline:
            rows.append(one_request(phase))
        return rows

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        for future in futures:
            measured.extend(future.result())
    elapsed = time.monotonic() - start
    return warmups, measured, elapsed


def hardware_record() -> dict[str, Any]:
    cpu_model = None
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware", "model")) and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                if cpu_model:
                    break
    return {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cpuModel": cpu_model,
        "cpuCount": os.cpu_count(),
        "python": platform.python_version(),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "githubRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "gitSha": os.environ.get("GITHUB_SHA"),
    }


class ServerController:
    def __init__(
        self,
        *,
        server: Path,
        model: Path,
        host: str,
        port: int,
        threads: int,
        threads_batch: int,
        parallel: int,
        context_size: int,
        ready_timeout: float,
        log_path: Path,
    ) -> None:
        self.server = server
        self.model = model
        self.host = host
        self.port = port
        self.threads = threads
        self.threads_batch = threads_batch
        self.parallel = parallel
        self.context_size = context_size
        self.ready_timeout = ready_timeout
        self.log_path = log_path
        self.process: subprocess.Popen[Any] | None = None
        self._log_handle: Any = None

    @property
    def command(self) -> list[str]:
        return [
            str(self.server),
            "-m",
            str(self.model),
            "-ngl",
            "0",
            "-c",
            str(self.context_size),
            "-np",
            str(self.parallel),
            "-cb",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "-t",
            str(self.threads),
            "-tb",
            str(self.threads_batch),
        ]

    def start(self, label: str) -> float:
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        self._log_handle.write(f"\n===== {label} {utc_now()} =====\n")
        self._log_handle.write("command: " + json.dumps(self.command) + "\n")
        self._log_handle.flush()
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return wait_ready(self.process, self.host, self.port, self.ready_timeout)

    def stop(self) -> dict[str, Any]:
        if self.process is None:
            return {"exitCode": None, "forced": False}
        proc = self.process
        forced = False
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # This is the harness's own child, not an arbitrary host PID.
                proc.kill()
                forced = True
                proc.wait(timeout=10)
        if self._log_handle is not None:
            self._log_handle.flush()
            self._log_handle.close()
        result = {"exitCode": proc.returncode, "forced": forced}
        self.process = None
        self._log_handle = None
        return result


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_requests(path: Path, rows: Iterable[RequestResult]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")


def write_telemetry(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = [
        "timestampUtc",
        "phase",
        "pid",
        "serverAlive",
        "rssMiB",
        "threads",
        "load1",
        "temperatureMaxC",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_checksums(out_dir: Path) -> None:
    lines: list[str] = []
    checksum_path = out_dir / "sha256sums.txt"
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file() and p != checksum_path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(out_dir)}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_concurrencies(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        value = int(part.strip())
        if value <= 0:
            raise argparse.ArgumentTypeError("concurrency values must be positive")
        if value not in values:
            values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one concurrency is required")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--threads", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--threads-batch", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--server-parallel", type=int, default=4)
    parser.add_argument("--context-size", type=int, default=2048)
    parser.add_argument("--capacity-concurrencies", default="1,2,4")
    parser.add_argument("--capacity-seconds", type=float, default=30)
    parser.add_argument("--soak-concurrency", type=int, default=4)
    parser.add_argument("--soak-seconds", type=float, default=300)
    parser.add_argument("--warmup-requests-per-worker", type=int, default=1)
    parser.add_argument("--restart-cycles", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=24)
    parser.add_argument("--request-timeout", type=float, default=60)
    parser.add_argument("--ready-timeout", type=float, default=120)
    parser.add_argument("--min-measured-requests", type=int, default=50)
    parser.add_argument("--max-error-rate", type=float, default=0)
    parser.add_argument("--max-ttft-p99-ms", type=float, default=5000)
    parser.add_argument("--max-e2e-p99-ms", type=float, default=30000)
    parser.add_argument("--max-recovery-seconds", type=float, default=120)
    parser.add_argument("--max-rss-mib", type=float, default=8192)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    server_log = out_dir / "llama-server.log"
    capacity = parse_concurrencies(args.capacity_concurrencies)
    hardware = hardware_record()
    started_at = utc_now()
    all_warmups: list[RequestResult] = []
    all_measured: list[RequestResult] = []
    telemetry: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    restarts: list[dict[str, Any]] = []
    infrastructure_error: str | None = None

    if not args.server.is_file() or not os.access(args.server, os.X_OK):
        infrastructure_error = f"server not executable: {args.server}"
    elif not args.model.is_file():
        infrastructure_error = f"model not found: {args.model}"

    controller = ServerController(
        server=args.server,
        model=args.model,
        host=args.host,
        port=args.port,
        threads=args.threads,
        threads_batch=args.threads_batch,
        parallel=max(args.server_parallel, max(capacity), args.soak_concurrency),
        context_size=args.context_size,
        ready_timeout=args.ready_timeout,
        log_path=server_log,
    )

    cold_ready_seconds: float | None = None
    stop_receipt: dict[str, Any] | None = None
    try:
        if infrastructure_error:
            raise RuntimeError(infrastructure_error)
        cold_ready_seconds = controller.start("cold-start")

        for concurrency in capacity:
            assert controller.process is not None
            phase = f"capacity-c{concurrency}"
            sampler = TelemetrySampler(controller.process, phase)
            sampler.start()
            warm, measured, elapsed = run_phase(
                host=args.host,
                port=args.port,
                phase="capacity",
                concurrency=concurrency,
                duration_seconds=args.capacity_seconds,
                warmup_requests=max(1, args.warmup_requests_per_worker * concurrency),
                max_tokens=args.max_tokens,
                request_timeout=args.request_timeout,
            )
            telemetry.extend(sampler.stop())
            all_warmups.extend(warm)
            all_measured.extend(measured)
            summaries.append(summarize_results("capacity", concurrency, measured, elapsed))
            if controller.process.poll() is not None:
                raise RuntimeError(
                    f"server exited unexpectedly after capacity c{concurrency} "
                    f"(rc={controller.process.returncode})"
                )

        assert controller.process is not None
        sampler = TelemetrySampler(controller.process, "soak")
        sampler.start()
        warm, measured, elapsed = run_phase(
            host=args.host,
            port=args.port,
            phase="soak",
            concurrency=args.soak_concurrency,
            duration_seconds=args.soak_seconds,
            warmup_requests=max(
                1, args.warmup_requests_per_worker * args.soak_concurrency
            ),
            max_tokens=args.max_tokens,
            request_timeout=args.request_timeout,
        )
        telemetry.extend(sampler.stop())
        all_warmups.extend(warm)
        all_measured.extend(measured)
        summaries.append(
            summarize_results("soak", args.soak_concurrency, measured, elapsed)
        )

        for cycle in range(1, args.restart_cycles + 1):
            stop_info = controller.stop()
            record: dict[str, Any] = {
                "cycle": cycle,
                "stop": stop_info,
                "readySeconds": None,
                "canary": None,
                "success": False,
                "error": None,
            }
            try:
                record["readySeconds"] = controller.start(f"restart-{cycle}")
                workload, prompt = PROMPTS[cycle % len(PROMPTS)]
                canary = stream_completion(
                    args.host,
                    args.port,
                    prompt,
                    args.max_tokens,
                    args.request_timeout,
                    phase="restart-canary",
                    concurrency=1,
                    workload=workload,
                )
                record["canary"] = asdict(canary)
                record["success"] = canary.status == "success"
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            restarts.append(record)
    except Exception as exc:
        infrastructure_error = f"{type(exc).__name__}: {exc}"
    finally:
        stop_receipt = controller.stop()

    gate = evaluate_gate(
        machine=hardware["machine"],
        summaries=summaries,
        restarts=restarts,
        telemetry=telemetry,
        min_requests=args.min_measured_requests,
        max_error_rate=args.max_error_rate,
        max_ttft_p99_ms=args.max_ttft_p99_ms,
        max_e2e_p99_ms=args.max_e2e_p99_ms,
        max_recovery_seconds=args.max_recovery_seconds,
        max_rss_mib=args.max_rss_mib,
    )
    if infrastructure_error:
        gate = {
            "verdict": "UNDETERMINED",
            "checks": gate["checks"],
            "infrastructureError": infrastructure_error,
        }

    summary = {
        "schema": SCHEMA,
        "startedAtUtc": started_at,
        "completedAtUtc": utc_now(),
        "hardware": hardware,
        "server": {
            "binary": str(args.server),
            "model": str(args.model),
            "command": controller.command,
            "coldReadySeconds": cold_ready_seconds,
            "finalStop": stop_receipt,
        },
        "workload": {
            "capacityConcurrencies": capacity,
            "capacitySecondsPerPoint": args.capacity_seconds,
            "soakConcurrency": args.soak_concurrency,
            "soakSeconds": args.soak_seconds,
            "maxTokens": args.max_tokens,
            "trafficFamilies": sorted({name for name, _ in PROMPTS}),
        },
        "totals": {
            "warmupRequests": len(all_warmups),
            "warmupErrors": sum(1 for r in all_warmups if r.status != "success"),
            "measuredRequests": len(all_measured),
            "measuredSuccesses": sum(
                1 for r in all_measured if r.status == "success"
            ),
            "measuredFailures": sum(
                1 for r in all_measured if r.status != "success"
            ),
        },
        "results": summaries,
        "restarts": restarts,
        "gate": gate,
        "actualProductionTraffic": False,
        "productionReady": False,
        "contestEvidenceCandidate": (
            hardware["machine"].lower() in {"aarch64", "arm64"}
            and gate["verdict"] == "PASS"
        ),
        "candidateOnly": True,
        "boundary": (
            "Synthetic single-host Arm64 readiness evidence. A PASS does not "
            "establish actual-production traffic, multi-day availability, "
            "failover, multi-tenant isolation, or customer correctness."
        ),
    }

    write_requests(out_dir / "warmup-requests.jsonl", all_warmups)
    write_requests(out_dir / "measured-requests.jsonl", all_measured)
    write_telemetry(out_dir / "telemetry.csv", telemetry)
    write_json(out_dir / "summary.json", summary)
    write_checksums(out_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))

    if gate["verdict"] == "PASS":
        return 0
    if gate["verdict"] == "FAIL":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
