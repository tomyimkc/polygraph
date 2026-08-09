#!/usr/bin/env python3
"""Capture, render, and validate the authoritative Polygraph contest video.

The renderer loads figures from committed repository evidence, combines 1080p
evidence cards with an auditable terminal replay from a real local ``make
demo`` execution, and encodes a web-friendly H.264/AAC MP4 with FFmpeg.  The
raw PTY transcript, timing events, receipt, and hashes remain beside the story
manifest so the project-function footage cannot silently become a mockup.

macOS can add narration with the built-in ``say`` command.  On other hosts use
``--tts none``; the output still contains a silent AAC track and the full
narration is written to a sidecar SRT.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import io
import json
import math
import os
import platform
import pty
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT_RESOLVED = REPO_ROOT.resolve(strict=True)
DEFAULT_STORY = Path("demo/contest-video/story.json")
DEFAULT_OUTPUT = Path("demo/out/polygraph-contest-final.mp4")
DEFAULT_CAPTURE_DIR = Path("demo/contest-video/live-run")
RENDERER_PATH = Path("tools/render_contest_video.py")
PRODUCTION_RECORD_PATH = Path("docs/VIDEO-PRODUCTION.md")
PRODUCTION_RECORD_BEGIN = "<!-- BEGIN AUTHORITATIVE VIDEO RECEIPT -->"
PRODUCTION_RECORD_END = "<!-- END AUTHORITATIVE VIDEO RECEIPT -->"
LIVE_CAPTURE_TIMING_NOTE = (
    "Literal PTY output from the captured run; line-reveal timing is normalized "
    "for legibility and the final frame is held. Output text is unchanged."
)
MAX_DURATION_SECONDS = 180.0
MIN_LIVE_FOOTAGE_SECONDS = 15.0
MAX_LIVE_FOOTAGE_SECONDS = 25.0
CAPTURE_TIMEOUT_SECONDS = 45.0
CAPTURE_TERM_GRACE_SECONDS = 3.0
MAX_RAW_TRANSCRIPT_BYTES = 1024 * 1024
MAX_CLEAN_TRANSCRIPT_BYTES = 512 * 1024
MAX_EVENT_COUNT = 4096
MAX_EVENTS_FILE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_GIF_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 128 * 1024
MAX_STORY_BYTES = 1024 * 1024
MAX_FINAL_MP4_BYTES = 100 * 1024 * 1024
EXPECTED_RECEIPT_FILENAMES = {
    "make-demo.ansi",
    "make-demo.txt",
    "make-demo.events.jsonl",
    "make-demo.gif",
}

ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r"
)

COLORS = {
    "amber": (251, 191, 36),
    "cyan": (34, 211, 238),
    "green": (74, 222, 128),
    "red": (248, 113, 113),
    "violet": (196, 181, 253),
}


class RenderError(RuntimeError):
    """A user-actionable render or evidence-validation failure."""


def validate_relative_path_text(raw: str | Path, *, field: str) -> Path:
    text = str(raw)
    if not text or "\x00" in text:
        raise RenderError(f"{field} must be a non-empty relative path")
    if "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise RenderError(f"{field} must use a repository-relative POSIX path")
    path = Path(text)
    if path.is_absolute():
        raise RenderError(f"{field} must be relative, not absolute: {text}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RenderError(f"{field} contains a forbidden path component: {text}")
    return path


def reject_symlink_components(candidate: Path, *, field: str) -> None:
    relative = candidate.relative_to(REPO_ROOT_RESOLVED)
    current = REPO_ROOT_RESOLVED
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RenderError(f"{field} must not traverse a symlink: {current}")


def resolve_repo_regular_file(raw: str | Path, *, field: str) -> Path:
    relative = validate_relative_path_text(raw, field=field)
    candidate = REPO_ROOT_RESOLVED / relative
    reject_symlink_components(candidate, field=field)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RenderError(f"{field} does not exist: {relative}") from exc
    if not resolved.is_relative_to(REPO_ROOT_RESOLVED):
        raise RenderError(f"{field} escapes the repository root: {relative}")
    mode = os.lstat(candidate).st_mode
    if not stat.S_ISREG(mode):
        raise RenderError(f"{field} must be a regular file: {relative}")
    return resolved


def resolve_repo_directory(
    raw: str | Path,
    *,
    field: str,
    create: bool,
) -> Path:
    relative = validate_relative_path_text(raw, field=field)
    candidate = REPO_ROOT_RESOLVED / relative
    reject_symlink_components(candidate, field=field)
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RenderError(f"{field} does not exist: {relative}") from exc
    if not resolved.is_relative_to(REPO_ROOT_RESOLVED):
        raise RenderError(f"{field} escapes the repository root: {relative}")
    mode = os.lstat(candidate).st_mode
    if not stat.S_ISDIR(mode):
        raise RenderError(f"{field} must be a directory: {relative}")
    return resolved


def resolve_repo_output_file(raw: str | Path, *, field: str) -> Path:
    relative = validate_relative_path_text(raw, field=field)
    candidate = REPO_ROOT_RESOLVED / relative
    parent_relative = relative.parent
    parent = resolve_repo_directory(
        parent_relative,
        field=f"{field} parent",
        create=True,
    )
    reject_symlink_components(candidate, field=field)
    if not parent.is_relative_to(REPO_ROOT_RESOLVED):
        raise RenderError(f"{field} parent escapes the repository root")
    if candidate.exists():
        mode = os.lstat(candidate).st_mode
        if not stat.S_ISREG(mode):
            raise RenderError(f"{field} must be a regular file: {relative}")
    return candidate


def expect_render_error(label: str, callback) -> None:
    try:
        callback()
    except RenderError:
        print(f"PASS security selftest: {label}")
        return
    raise RenderError(f"security selftest did not reject: {label}")


def run_security_selftests() -> None:
    selftest_parent = resolve_repo_directory(
        Path("demo/out"),
        field="selftest parent",
        create=True,
    )
    with tempfile.TemporaryDirectory(
        prefix="security-selftest-",
        dir=selftest_parent,
    ) as temp_name:
        temp_dir = Path(temp_name)
        valid = temp_dir / "valid.txt"
        valid.write_text("ok\n", encoding="utf-8")
        valid_relative = valid.relative_to(REPO_ROOT_RESOLVED)
        resolved = resolve_repo_regular_file(valid_relative, field="selftest valid")
        if resolved != valid.resolve(strict=True):
            raise RenderError("security selftest failed valid contained-file resolution")
        print("PASS security selftest: contained regular file accepted")

        expect_render_error(
            "absolute outside path",
            lambda: resolve_repo_regular_file("/etc/passwd", field="selftest outside"),
        )
        expect_render_error(
            "parent traversal",
            lambda: resolve_repo_regular_file(
                "demo/out/../contest-video/story.json",
                field="selftest traversal",
            ),
        )
        inside_link = temp_dir / "inside-link"
        inside_link.symlink_to(valid)
        expect_render_error(
            "symlink to contained file",
            lambda: resolve_repo_regular_file(
                inside_link.relative_to(REPO_ROOT_RESOLVED),
                field="selftest inside symlink",
            ),
        )
        outside_link = temp_dir / "outside-link"
        outside_link.symlink_to("/etc/passwd")
        expect_render_error(
            "symlink to outside file",
            lambda: resolve_repo_regular_file(
                outside_link.relative_to(REPO_ROOT_RESOLVED),
                field="selftest outside symlink",
            ),
        )
        expect_render_error(
            "unknown receipt key",
            lambda: require_exact_keys(
                {"known": 1, "unknown": 2},
                {"known"},
                field="selftest receipt",
            ),
        )
        expect_render_error(
            "unknown receipt filename",
            lambda: require_exact_keys(
                {**{name: "0" * 64 for name in EXPECTED_RECEIPT_FILENAMES}, "extra": "0" * 64},
                EXPECTED_RECEIPT_FILENAMES,
                field="selftest receipt files",
            ),
        )
    print("security selftests: OK")


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command))
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise RenderError(f"required evidence file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_command_paths(names: Iterable[str]) -> dict[str, str]:
    resolved_paths: dict[str, str] = {}
    for name in names:
        discovered = shutil.which(name)
        if not discovered:
            raise RenderError(f"required command not found on PATH: {name}")
        try:
            resolved_paths[name] = str(Path(discovered).resolve(strict=True))
        except FileNotFoundError as exc:
            raise RenderError(
                f"resolved command path does not exist for {name}: {discovered}"
            ) from exc
    return resolved_paths


def command_first_line(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return None
    lines = completed.stdout.strip().splitlines()
    return lines[0] if lines else None


def strip_terminal_capture(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = ANSI_ESCAPE.sub("", text)
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def terminate_owned_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + CAPTURE_TERM_GRACE_SECONDS
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def capture_pty(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[int, float, bytes, list[dict[str, Any]]]:
    master_fd, slave_fd = pty.openpty()
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    raw = bytearray()
    events: list[dict[str, Any]] = []
    eof = False
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed > CAPTURE_TIMEOUT_SECONDS:
                terminate_owned_process_group(process)
                raise RenderError(
                    f"make demo capture exceeded {CAPTURE_TIMEOUT_SECONDS:.0f}s timeout"
                )
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            received = False
            if ready:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    received = True
                    if len(raw) + len(chunk) > MAX_RAW_TRANSCRIPT_BYTES:
                        terminate_owned_process_group(process)
                        raise RenderError(
                            "make demo capture exceeded raw transcript size limit "
                            f"({MAX_RAW_TRANSCRIPT_BYTES} bytes)"
                        )
                    raw.extend(chunk)
                    if len(events) >= MAX_EVENT_COUNT:
                        terminate_owned_process_group(process)
                        raise RenderError(
                            f"make demo capture exceeded {MAX_EVENT_COUNT} PTY events"
                        )
                    events.append(
                        {
                            "t": round(time.monotonic() - started, 6),
                            "dataBase64": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                else:
                    eof = True
            if process.poll() is not None and (eof or (not ready and not received)):
                break
    except BaseException:
        terminate_owned_process_group(process)
        raise
    finally:
        os.close(master_fd)
    exit_code = process.wait()
    return exit_code, time.monotonic() - started, bytes(raw), events


def verify_demo_transcript(text: str) -> None:
    required = [
        "using fast path: yes",
        "MISMATCH:",
        "L3 (dispatch)   0 hits",
        "exit code: 1",
        "MATCH:",
        "L3 (dispatch)   1 hits",
        "exit code: 0",
        "PASS: liar   -> exit 1 (MISMATCH), as expected",
        "PASS: honest -> exit 0 (MATCH), as expected",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    if missing:
        raise RenderError(
            "captured make demo output is incomplete; missing:\n- "
            + "\n- ".join(missing)
        )


def capture_make_demo(capture_dir: Path) -> tuple[Path, list[Path], dict[str, Any]]:
    """Run ``make demo`` from an isolated archive of HEAD and retain the receipt."""

    resolve_command_paths(("git", "make", "cc", "lldb", "python3"))
    if not capture_dir.is_relative_to(REPO_ROOT_RESOLVED):
        raise RenderError("capture directory must be contained under the repository root")
    reject_symlink_components(capture_dir, field="capture directory")
    capture_dir.mkdir(parents=True, exist_ok=True)
    capture_dir = capture_dir.resolve(strict=True)

    command_paths = resolve_command_paths(("make", "cc", "lldb", "python3"))

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    archive_run = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    archive_bytes = archive_run.stdout

    started_utc = dt.datetime.now(dt.timezone.utc)
    scratch_parent = Path("/private/tmp")
    if not scratch_parent.is_dir():
        scratch_parent = Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(
        prefix="polygraph-contest-live-run-", dir=scratch_parent
    ) as scratch_name:
        scratch = Path(scratch_name)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            archive.extractall(scratch, filter="data")
        source_file_names = [
            "Makefile",
            "examples/catch-a-liar/demo.sh",
            "examples/catch-a-liar/liar.c",
            "tools/polygraph",
            "tools/verify_dispatch.py",
            "tools/targets/catch-a-liar.json",
        ]
        source_files: dict[str, str] = {}
        for relative_name in source_file_names:
            source_path = scratch / relative_name
            if source_path.is_symlink() or not source_path.is_file():
                raise RenderError(
                    f"capture source must be a regular non-symlink file: {relative_name}"
                )
            source_files[relative_name] = sha256(source_path)

        isolated_home = scratch / ".capture-home"
        isolated_tmp = scratch / ".capture-tmp"
        isolated_home.mkdir(mode=0o700)
        isolated_tmp.mkdir(mode=0o700)
        path_dirs = {
            str(Path(command_paths[name]).parent)
            for name in ("make", "cc", "lldb", "python3")
        }
        path_dirs.update({"/usr/bin", "/bin", "/usr/sbin", "/sbin"})
        capture_env = {
            "PATH": os.pathsep.join(sorted(path_dirs)),
            "HOME": str(isolated_home),
            "TMPDIR": str(isolated_tmp) + os.sep,
            "TERM": "xterm-256color",
            "LC_ALL": "C",
            "LANG": "C",
            "CC": command_paths["cc"],
            "PYTHONHASHSEED": "0",
        }
        exit_code, wall_seconds, raw, events = capture_pty(
            [command_paths["make"], "demo"],
            cwd=scratch,
            env=capture_env,
        )
        host_metadata = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "compiler": command_first_line(
                [command_paths["cc"], "--version"],
                env=capture_env,
                cwd=scratch,
            ),
            "debugger": command_first_line(
                [command_paths["lldb"], "--version"],
                env=capture_env,
                cwd=scratch,
            ),
            "make": command_first_line(
                [command_paths["make"], "--version"],
                env=capture_env,
                cwd=scratch,
            ),
        }
    completed_utc = dt.datetime.now(dt.timezone.utc)

    capture_relative = capture_dir.relative_to(REPO_ROOT_RESOLVED)
    raw_path = resolve_repo_output_file(
        capture_relative / "make-demo.ansi",
        field="capture raw transcript",
    )
    text_path = resolve_repo_output_file(
        capture_relative / "make-demo.txt",
        field="capture clean transcript",
    )
    events_path = resolve_repo_output_file(
        capture_relative / "make-demo.events.jsonl",
        field="capture event log",
    )
    gif_path = resolve_repo_output_file(
        capture_relative / "make-demo.gif",
        field="capture GIF",
    )
    receipt_path = resolve_repo_output_file(
        capture_relative / "receipt.json",
        field="capture receipt",
    )
    sums_path = resolve_repo_output_file(
        capture_relative / "SHA256SUMS",
        field="capture SHA256SUMS",
    )

    if exit_code != 0:
        raise RenderError(f"captured make demo exited {exit_code}; refusing to use the footage")
    raw_path.write_bytes(raw)
    clean_text = strip_terminal_capture(raw)
    if len(clean_text.encode("utf-8")) > MAX_CLEAN_TRANSCRIPT_BYTES:
        raise RenderError(
            "clean make-demo transcript exceeded size limit "
            f"({MAX_CLEAN_TRANSCRIPT_BYTES} bytes)"
        )
    text_path.write_text(clean_text, encoding="utf-8")
    events_text = "".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        for event in events
    )
    if len(events_text.encode("utf-8")) > MAX_EVENTS_FILE_BYTES:
        raise RenderError(
            f"make-demo event log exceeded {MAX_EVENTS_FILE_BYTES} bytes"
        )
    events_path.write_text(events_text, encoding="utf-8")
    verify_demo_transcript(clean_text)
    render_terminal_capture_gif(text_path, gif_path, target_seconds=20.0)
    if gif_path.stat().st_size > MAX_CAPTURE_GIF_BYTES:
        raise RenderError(
            f"captured GIF exceeds {MAX_CAPTURE_GIF_BYTES} byte limit"
        )
    gif_probe = ffprobe(gif_path)

    receipt = {
        "schema": "polygraph.contest-video.live-capture.v1",
        "command": ["make", "demo"],
        "source": {
            "branch": branch,
            "gitHead": git_head,
            "gitArchiveSha256": hashlib.sha256(archive_bytes).hexdigest(),
            "workingTreeEditsIncluded": False,
            "method": "git archive HEAD extracted to an isolated temporary directory",
            "captureDriver": str(RENDERER_PATH),
            "captureDriverSha256": sha256(REPO_ROOT_RESOLVED / RENDERER_PATH),
            "files": source_files,
        },
        "run": {
            "startedAtUtc": started_utc.isoformat(),
            "completedAtUtc": completed_utc.isoformat(),
            "wallSeconds": round(wall_seconds, 6),
            "exitCode": exit_code,
            "ptyEventCount": len(events),
        },
        "capturePolicy": {
            "environmentMode": "strict-allowlist",
            "environmentKeys": sorted(capture_env),
            "commandPaths": command_paths,
            "timeoutSeconds": CAPTURE_TIMEOUT_SECONDS,
            "termGraceSeconds": CAPTURE_TERM_GRACE_SECONDS,
            "maxRawTranscriptBytes": MAX_RAW_TRANSCRIPT_BYTES,
            "maxCleanTranscriptBytes": MAX_CLEAN_TRANSCRIPT_BYTES,
            "maxEventCount": MAX_EVENT_COUNT,
            "maxEventsFileBytes": MAX_EVENTS_FILE_BYTES,
            "maxGifBytes": MAX_CAPTURE_GIF_BYTES,
            "processGroup": "new session; SIGTERM then SIGKILL only for owned group",
        },
        "host": host_metadata,
        "verifiedOutput": {
            "liarBanner": "using fast path: yes",
            "liarL3Hits": 0,
            "liarExitCode": 1,
            "honestBanner": "using fast path: yes",
            "honestL3Hits": 1,
            "honestExitCode": 0,
            "overallExitCode": exit_code,
        },
        "playback": {
            "asset": str(gif_path.relative_to(REPO_ROOT)),
            "durationSeconds": float(gif_probe["format"]["duration"]),
            "timing": LIVE_CAPTURE_TIMING_NOTE,
            "fabricatedOutput": False,
        },
        "files": {
            str(raw_path.name): sha256(raw_path),
            str(text_path.name): sha256(text_path),
            str(events_path.name): sha256(events_path),
            str(gif_path.name): sha256(gif_path),
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RenderError(f"capture receipt exceeds {MAX_RECEIPT_BYTES} byte limit")
    captured_paths = [raw_path, text_path, events_path, gif_path, receipt_path]
    sums_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in captured_paths),
        encoding="utf-8",
    )
    captured_paths.append(sums_path)
    print(
        f"captured real make demo: {wall_seconds:.3f}s, exit {exit_code}, "
        f"playback {receipt['playback']['durationSeconds']:.3f}s"
    )
    return receipt_path, captured_paths, receipt


def find_scale_row(rows: Iterable[dict[str, Any]], row_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("id") == row_id:
            return row
    raise RenderError(f"scale evidence row not found: {row_id}")


def load_facts() -> tuple[dict[str, Any], list[Path]]:
    """Load every displayed figure from committed evidence and cross-check it."""

    provenance_path = REPO_ROOT / "results" / "server" / "spark-provenance.txt"
    scale_path = REPO_ROOT / "results" / "scale" / "scale-experiment.json"
    l3_path = (
        REPO_ROOT
        / "results"
        / "upstream"
        / "llamacpp-26334-cuda-host-buffer.json"
    )
    arm_summary_path = (
        REPO_ROOT
        / "results"
        / "production-readiness"
        / "arm64-31294460364"
        / "summary.json"
    )
    arm_validation_path = arm_summary_path.with_name("validation-receipt.json")
    arm_workflow_path = arm_summary_path.with_name("workflow-receipt.json")
    pro_path = (
        REPO_ROOT
        / "results"
        / "production-readiness"
        / "pro6000-31289517517"
        / "aggregate.json"
    )
    tool_path = REPO_ROOT / "tools" / "polygraph"
    verify_path = REPO_ROOT / "tools" / "verify_dispatch.py"

    required_paths = [
        provenance_path,
        scale_path,
        l3_path,
        arm_summary_path,
        arm_validation_path,
        arm_workflow_path,
        pro_path,
        tool_path,
        verify_path,
    ]
    for path in required_paths:
        if not path.is_file():
            raise RenderError(f"required evidence/tool file is missing: {path}")

    provenance = provenance_path.read_text(encoding="utf-8")
    broken_match = re.search(
        r"\[DEFAULT BUILD:[^\]]+\]\s+kai_run_matmul symbols:\s*(\d+)",
        provenance,
    )
    fixed_match = re.search(
        r"\[FIXED BUILD:\s*\+\s*([^\]]+)\]\s+kai_run_matmul symbols:\s*(\d+)",
        provenance,
    )
    if not broken_match or not fixed_match:
        raise RenderError("could not parse broken/fixed symbol counts from spark-provenance.txt")
    broken_symbols = int(broken_match.group(1))
    build_fix = fixed_match.group(1).strip()
    fixed_symbols = int(fixed_match.group(2))
    if broken_symbols != 0 or fixed_symbols != 10:
        raise RenderError(
            "unexpected Spark symbol counts; refusing to render stale narration: "
            f"broken={broken_symbols}, fixed={fixed_symbols}"
        )
    if "KLEIDIAI = 1" not in provenance:
        raise RenderError("Spark provenance no longer contains the KLEIDIAI = 1 banner")

    scale = load_json(scale_path)
    broken_7b = find_scale_row(scale["B_finding3_cost"], "7B BROKEN build")
    fixed_7b = find_scale_row(scale["B_finding3_cost"], "7B FIXED build")
    prefill_ratio = fixed_7b["prefill_median"] / broken_7b["prefill_median"]
    decode_ratio = fixed_7b["decode_median"] / broken_7b["decode_median"]
    if not math.isclose(prefill_ratio, 4.5670, rel_tol=0.001):
        raise RenderError(f"unexpected 7B prefill ratio: {prefill_ratio}")
    if not math.isclose(decode_ratio, 1.6517, rel_tol=0.002):
        raise RenderError(f"unexpected 7B decode ratio: {decode_ratio}")

    l3 = load_json(l3_path)
    summary = l3["interleaved_sweep"]["summary"]
    baseline_hits = int(summary["A"]["median_hits"])
    workaround_hits = int(summary["B"]["median_hits"])
    baseline_all = [int(value) for value in summary["A"]["hits_all_reps"]]
    workaround_all = [int(value) for value in summary["B"]["hits_all_reps"]]
    if baseline_hits != 0 or any(baseline_all):
        raise RenderError("L3 baseline is no longer five zero-hit repetitions")
    if workaround_hits != 7968 or set(workaround_all) != {7968}:
        raise RenderError("L3 --no-host repetitions no longer agree at 7,968 hits")
    if len(baseline_all) != len(workaround_all):
        raise RenderError("L3 interleaved arms have different repetition counts")

    arm = load_json(arm_summary_path)
    arm_validation = load_json(arm_validation_path)
    arm_workflow = load_json(arm_workflow_path)
    if str(arm["hardware"]["githubRunId"]) != "31294460364":
        raise RenderError("Arm64 summary run ID drifted from 31294460364")
    if int(arm_validation["runId"]) != 31294460364:
        raise RenderError("Arm64 validation receipt run ID does not match")
    if int(arm_workflow["runId"]) != 31294460364:
        raise RenderError("Arm64 workflow receipt run ID does not match")
    if arm["gate"]["verdict"] != "PASS":
        raise RenderError("Arm64 configured readiness gate is not PASS")
    if arm["productionReady"] is not False or arm["candidateOnly"] is not True:
        raise RenderError("Arm64 claim-boundary flags changed; narration must be reviewed")
    soak = next(row for row in arm["results"] if row["phase"] == "soak")
    restarts = arm["restarts"]

    pro = load_json(pro_path)
    if pro["armContestEvidence"] is not False:
        raise RenderError("Pro 6000 artifact is no longer explicitly non-Arm evidence")
    if pro["actualProductionTraffic"] is not False:
        raise RenderError("Pro 6000 artifact unexpectedly claims production traffic")

    build_fix_short = "GGML_NATIVE=OFF + explicit Armv9.2/SVE2/I8MM flags"

    facts = {
        "broken_symbols": broken_symbols,
        "fixed_symbols": fixed_symbols,
        "build_fix": build_fix,
        "build_fix_short": build_fix_short,
        "scale_reps": int(scale["reps"]),
        "large_broken_prefill": float(broken_7b["prefill_median"]),
        "large_fixed_prefill": float(fixed_7b["prefill_median"]),
        "large_prefill_ratio": prefill_ratio,
        "large_broken_decode": float(broken_7b["decode_median"]),
        "large_fixed_decode": float(fixed_7b["decode_median"]),
        "large_decode_ratio": decode_ratio,
        "l3_baseline_hits": baseline_hits,
        "l3_workaround_hits": workaround_hits,
        "l3_workaround_hits_commas": f"{workaround_hits:,}",
        "l3_reps": len(baseline_all),
        "arm_run_id": str(arm["hardware"]["githubRunId"]),
        "arm_requests": int(arm["totals"]["measuredRequests"]),
        "arm_requests_commas": f"{int(arm['totals']['measuredRequests']):,}",
        "arm_failures": int(arm["totals"]["measuredFailures"]),
        "arm_gate_passed": sum(1 for check in arm["gate"]["checks"] if check["passed"]),
        "arm_gate_total": len(arm["gate"]["checks"]),
        "arm_machine": arm["hardware"]["machine"],
        "arm_cpu_count": int(arm["hardware"]["cpuCount"]),
        "arm_soak_minutes": int(round(float(arm["workload"]["soakSeconds"]) / 60)),
        "arm_soak_concurrency": int(arm["workload"]["soakConcurrency"]),
        "arm_soak_output_tps": float(soak["outputTokensPerSecond"]),
        "arm_ttft_p99_ms": float(soak["ttftP99Ms"]),
        "arm_e2e_p99_ms": float(soak["e2eP99Ms"]),
        "arm_restart_cycles": len(restarts),
        "arm_restart_max_seconds": max(float(item["readySeconds"]) for item in restarts),
        "pro_model": str(pro["model"]),
        "pro_model_short": "30B-A3B",
    }
    return facts, required_paths


def format_value(value: Any, facts: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format_map(facts)
        except KeyError as exc:
            raise RenderError(f"story references unknown fact: {exc}") from exc
    if isinstance(value, list):
        return [format_value(item, facts) for item in value]
    if isinstance(value, dict):
        return {key: format_value(item, facts) for key, item in value.items()}
    return value


def load_story(path: Path, facts: dict[str, Any]) -> dict[str, Any]:
    story = format_value(load_json(path), facts)
    scenes = story.get("scenes", [])
    if not scenes:
        raise RenderError("story contains no scenes")
    duration = sum(float(scene["duration_seconds"]) for scene in scenes)
    declared = float(story["target_duration_seconds"])
    if not math.isclose(duration, declared, abs_tol=0.001):
        raise RenderError(
            f"story duration mismatch: scenes total {duration:.3f}s, declared {declared:.3f}s"
        )
    if duration >= MAX_DURATION_SECONDS:
        raise RenderError(
            f"story is {duration:.3f}s; contest output must be under {MAX_DURATION_SECONDS:.0f}s"
        )
    required_order = [
        "01-banner",
        "02-l3",
        "03-cost",
        "04-fixes-tooling",
        "05-arm64-readiness",
        "06-boundary",
    ]
    actual_order = [scene["id"] for scene in scenes]
    if actual_order != required_order:
        raise RenderError(f"story order drifted: expected {required_order}, got {actual_order}")
    live_scenes = [scene for scene in scenes if scene.get("live_capture")]
    if len(live_scenes) != 1:
        raise RenderError(
            f"story must contain exactly one live project-function scene, found {len(live_scenes)}"
        )
    live = live_scenes[0]["live_capture"]
    static_seconds = float(live["static_seconds"])
    footage_seconds = float(live["footage_seconds"])
    if not MIN_LIVE_FOOTAGE_SECONDS <= footage_seconds <= MAX_LIVE_FOOTAGE_SECONDS:
        raise RenderError(
            f"live footage is {footage_seconds:.3f}s; required range is "
            f"{MIN_LIVE_FOOTAGE_SECONDS:.0f}–{MAX_LIVE_FOOTAGE_SECONDS:.0f}s"
        )
    if not math.isclose(
        static_seconds + footage_seconds,
        float(live_scenes[0]["duration_seconds"]),
        abs_tol=0.001,
    ):
        raise RenderError(
            "live scene static_seconds + footage_seconds must equal scene duration"
        )
    return story


def require_exact_keys(
    value: Any,
    expected: set[str],
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{field} must be an object")
    actual = set(value)
    if actual != expected:
        raise RenderError(
            f"{field} keys differ; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def validate_sha256sums(
    sums_path: Path,
    *,
    expected_paths: dict[str, Path],
) -> None:
    if sums_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RenderError("live capture SHA256SUMS exceeds size limit")
    parsed: dict[str, str] = {}
    for line_number, line in enumerate(
        sums_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match:
            raise RenderError(
                f"invalid SHA256SUMS line {line_number}: {line!r}"
            )
        digest, filename = match.groups()
        if filename in parsed:
            raise RenderError(f"duplicate SHA256SUMS filename: {filename}")
        parsed[filename] = digest
    if set(parsed) != set(expected_paths):
        raise RenderError(
            "SHA256SUMS filenames differ; "
            f"missing={sorted(set(expected_paths) - set(parsed))}, "
            f"unknown={sorted(set(parsed) - set(expected_paths))}"
        )
    for filename, path in expected_paths.items():
        actual = sha256(path)
        if parsed[filename] != actual:
            raise RenderError(
                f"SHA256SUMS mismatch for {filename}: "
                f"expected {parsed[filename]}, got {actual}"
            )


def reconstruct_event_bytes(events_path: Path, *, wall_seconds: float) -> bytes:
    if events_path.stat().st_size > MAX_EVENTS_FILE_BYTES:
        raise RenderError("live capture event log exceeds size limit")
    chunks: list[bytes] = []
    previous_time = -1.0
    lines = events_path.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_EVENT_COUNT:
        raise RenderError("live capture event count exceeds limit")
    for index, line in enumerate(lines, start=1):
        event = require_exact_keys(
            json.loads(line),
            {"t", "dataBase64"},
            field=f"live capture event {index}",
        )
        timestamp = event["t"]
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise RenderError(f"live capture event {index} timestamp is not numeric")
        timestamp = float(timestamp)
        if timestamp < previous_time or timestamp < 0 or timestamp > wall_seconds + 1:
            raise RenderError(f"live capture event {index} timestamp is out of bounds")
        previous_time = timestamp
        data_b64 = event["dataBase64"]
        if not isinstance(data_b64, str):
            raise RenderError(f"live capture event {index} dataBase64 is not a string")
        try:
            chunk = base64.b64decode(data_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RenderError(f"live capture event {index} has invalid base64") from exc
        chunks.append(chunk)
        if sum(len(item) for item in chunks) > MAX_RAW_TRANSCRIPT_BYTES:
            raise RenderError("reconstructed live capture exceeds transcript size limit")
    return b"".join(chunks)


def resolve_live_capture(
    story: dict[str, Any],
) -> tuple[Path, Path, list[Path], dict[str, Any]]:
    scene = next(scene for scene in story["scenes"] if scene.get("live_capture"))
    config = require_exact_keys(
        scene["live_capture"],
        {
            "asset",
            "receipt",
            "static_seconds",
            "footage_seconds",
            "command",
            "timing_note",
        },
        field="story live_capture",
    )
    try:
        asset = resolve_repo_regular_file(config["asset"], field="story live asset")
        receipt_path = resolve_repo_regular_file(
            config["receipt"],
            field="story live receipt",
        )
    except RenderError as exc:
        raise RenderError(
            f"{exc}; regenerate with python3 tools/render_contest_video.py --capture-demo"
        ) from exc
    if receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RenderError("live capture receipt exceeds size limit")
    receipt = load_json(receipt_path)
    receipt = require_exact_keys(
        receipt,
        {
            "schema",
            "command",
            "source",
            "run",
            "capturePolicy",
            "host",
            "verifiedOutput",
            "playback",
            "files",
        },
        field="live capture receipt",
    )
    if receipt["schema"] != "polygraph.contest-video.live-capture.v1":
        raise RenderError("live capture receipt schema is missing or unsupported")
    if receipt["command"] != ["make", "demo"] or config["command"] != "make demo":
        raise RenderError("live capture command is not exactly make demo")

    source = require_exact_keys(
        receipt["source"],
        {
            "branch",
            "gitHead",
            "gitArchiveSha256",
            "workingTreeEditsIncluded",
            "method",
            "captureDriver",
            "captureDriverSha256",
            "files",
        },
        field="live capture source",
    )
    source_files = require_exact_keys(
        source["files"],
        {
            "Makefile",
            "examples/catch-a-liar/demo.sh",
            "examples/catch-a-liar/liar.c",
            "tools/polygraph",
            "tools/verify_dispatch.py",
            "tools/targets/catch-a-liar.json",
        },
        field="live capture source files",
    )
    run_data = require_exact_keys(
        receipt["run"],
        {
            "startedAtUtc",
            "completedAtUtc",
            "wallSeconds",
            "exitCode",
            "ptyEventCount",
        },
        field="live capture run",
    )
    capture_policy = require_exact_keys(
        receipt["capturePolicy"],
        {
            "environmentMode",
            "environmentKeys",
            "commandPaths",
            "timeoutSeconds",
            "termGraceSeconds",
            "maxRawTranscriptBytes",
            "maxCleanTranscriptBytes",
            "maxEventCount",
            "maxEventsFileBytes",
            "maxGifBytes",
            "processGroup",
        },
        field="live capture policy",
    )
    require_exact_keys(
        capture_policy["commandPaths"],
        {"make", "cc", "lldb", "python3"},
        field="live capture command paths",
    )
    require_exact_keys(
        receipt["host"],
        {"platform", "machine", "python", "compiler", "debugger", "make"},
        field="live capture host",
    )
    verified_output = require_exact_keys(
        receipt["verifiedOutput"],
        {
            "liarBanner",
            "liarL3Hits",
            "liarExitCode",
            "honestBanner",
            "honestL3Hits",
            "honestExitCode",
            "overallExitCode",
        },
        field="live capture verifiedOutput",
    )
    playback = require_exact_keys(
        receipt["playback"],
        {"asset", "durationSeconds", "timing", "fabricatedOutput"},
        field="live capture playback",
    )
    files = require_exact_keys(
        receipt["files"],
        EXPECTED_RECEIPT_FILENAMES,
        field="live capture files",
    )
    if run_data["exitCode"] != 0:
        raise RenderError("live make-demo receipt does not record a successful overall run")
    if playback["fabricatedOutput"] is not False:
        raise RenderError("live capture receipt does not explicitly deny fabricated output")
    if verified_output != {
        "liarBanner": "using fast path: yes",
        "liarL3Hits": 0,
        "liarExitCode": 1,
        "honestBanner": "using fast path: yes",
        "honestL3Hits": 1,
        "honestExitCode": 0,
        "overallExitCode": 0,
    }:
        raise RenderError("live capture verified-output contract changed")
    if capture_policy["environmentMode"] != "strict-allowlist":
        raise RenderError("live capture did not use the strict allowlisted environment")
    expected_environment_keys = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TERM",
        "LC_ALL",
        "LANG",
        "CC",
        "PYTHONHASHSEED",
    }
    if set(capture_policy["environmentKeys"]) != expected_environment_keys:
        raise RenderError("live capture environment allowlist keys changed")
    current_command_paths = resolve_command_paths(("make", "cc", "lldb", "python3"))
    if capture_policy["commandPaths"] != current_command_paths:
        raise RenderError("live capture command paths differ from the reviewed host tools")
    expected_policy_values = {
        "timeoutSeconds": CAPTURE_TIMEOUT_SECONDS,
        "termGraceSeconds": CAPTURE_TERM_GRACE_SECONDS,
        "maxRawTranscriptBytes": MAX_RAW_TRANSCRIPT_BYTES,
        "maxCleanTranscriptBytes": MAX_CLEAN_TRANSCRIPT_BYTES,
        "maxEventCount": MAX_EVENT_COUNT,
        "maxEventsFileBytes": MAX_EVENTS_FILE_BYTES,
        "maxGifBytes": MAX_CAPTURE_GIF_BYTES,
    }
    for key, expected in expected_policy_values.items():
        if capture_policy[key] != expected:
            raise RenderError(f"live capture policy {key} changed")
    if (
        capture_policy["processGroup"]
        != "new session; SIGTERM then SIGKILL only for owned group"
    ):
        raise RenderError("live capture process-group policy changed")
    if not isinstance(run_data["wallSeconds"], (int, float)) or isinstance(
        run_data["wallSeconds"], bool
    ):
        raise RenderError("live capture wallSeconds is not numeric")
    if not 0 <= float(run_data["wallSeconds"]) <= CAPTURE_TIMEOUT_SECONDS:
        raise RenderError("live capture wallSeconds exceeds timeout policy")
    if not isinstance(run_data["ptyEventCount"], int) or isinstance(
        run_data["ptyEventCount"], bool
    ):
        raise RenderError("live capture ptyEventCount is not an integer")
    if not 0 < run_data["ptyEventCount"] <= MAX_EVENT_COUNT:
        raise RenderError("live capture ptyEventCount is outside policy")
    try:
        started = dt.datetime.fromisoformat(run_data["startedAtUtc"])
        completed = dt.datetime.fromisoformat(run_data["completedAtUtc"])
    except (TypeError, ValueError) as exc:
        raise RenderError("live capture UTC timestamps are invalid") from exc
    if started.tzinfo is None or completed.tzinfo is None or completed < started:
        raise RenderError("live capture UTC timestamps are unordered or timezone-free")
    if source["workingTreeEditsIncluded"] is not False:
        raise RenderError("live capture included unreviewed working-tree edits")
    if source["method"] != "git archive HEAD extracted to an isolated temporary directory":
        raise RenderError("live capture source-isolation method changed")
    if source["captureDriver"] != str(RENDERER_PATH):
        raise RenderError("live capture driver path changed")
    if source["captureDriverSha256"] != sha256(REPO_ROOT_RESOLVED / RENDERER_PATH):
        raise RenderError(
            "live make-demo capture is stale for the current reviewed capture driver"
        )
    if config["timing_note"] != LIVE_CAPTURE_TIMING_NOTE:
        raise RenderError("story live-capture timing disclosure changed")
    if playback["timing"] != LIVE_CAPTURE_TIMING_NOTE:
        raise RenderError("live capture timing disclosure differs from the story")

    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if source["gitHead"] != current_head or source["branch"] != current_branch:
        raise RenderError(
            "live make-demo capture is stale for the current reviewed branch/HEAD"
        )
    archive_bytes = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    if hashlib.sha256(archive_bytes).hexdigest() != source["gitArchiveSha256"]:
        raise RenderError("live capture git archive hash does not match current HEAD")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for filename, expected_hash in source_files.items():
            member = archive.getmember(filename)
            if not member.isfile():
                raise RenderError(f"reviewed capture source is not regular: {filename}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RenderError(f"could not read reviewed capture source: {filename}")
            actual_hash = hashlib.sha256(extracted.read()).hexdigest()
            if actual_hash != expected_hash:
                raise RenderError(f"reviewed capture source hash changed: {filename}")

    capture_dir = receipt_path.parent
    capture_paths: list[Path] = []
    receipt_relative_dir = capture_dir.relative_to(REPO_ROOT_RESOLVED)
    file_paths: dict[str, Path] = {}
    for filename, expected_hash in files.items():
        if filename not in EXPECTED_RECEIPT_FILENAMES:
            raise RenderError(f"unknown live capture filename: {filename}")
        if Path(filename).name != filename:
            raise RenderError(f"live capture filename must be a basename: {filename}")
        path = resolve_repo_regular_file(
            receipt_relative_dir / filename,
            field=f"live capture receipt file {filename}",
        )
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise RenderError(
                f"live capture hash mismatch for {path.name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        capture_paths.append(path)
        file_paths[filename] = path
    if playback["asset"] != config["asset"]:
        raise RenderError("receipt playback asset differs from story asset")
    if sha256(asset) != files[asset.name]:
        raise RenderError("live footage asset hash does not match its capture receipt")
    if not math.isclose(
        float(playback["durationSeconds"]),
        float(config["footage_seconds"]),
        abs_tol=0.05,
    ):
        raise RenderError("live playback duration differs from the story footage duration")

    raw_path = file_paths["make-demo.ansi"]
    clean_path = file_paths["make-demo.txt"]
    events_path = file_paths["make-demo.events.jsonl"]
    if raw_path.stat().st_size > MAX_RAW_TRANSCRIPT_BYTES:
        raise RenderError("live raw transcript exceeds size limit")
    if clean_path.stat().st_size > MAX_CLEAN_TRANSCRIPT_BYTES:
        raise RenderError("live clean transcript exceeds size limit")
    raw = raw_path.read_bytes()
    reconstructed = reconstruct_event_bytes(
        events_path,
        wall_seconds=float(run_data["wallSeconds"]),
    )
    if reconstructed != raw:
        raise RenderError("live event stream does not reconstruct the raw PTY transcript")
    expected_clean = strip_terminal_capture(raw)
    if clean_path.read_text(encoding="utf-8") != expected_clean:
        raise RenderError("clean transcript is not derived from the bound raw PTY transcript")
    verify_demo_transcript(expected_clean)
    if int(run_data["ptyEventCount"]) != len(
        events_path.read_text(encoding="utf-8").splitlines()
    ):
        raise RenderError("live receipt PTY event count does not match events file")
    if asset.stat().st_size > MAX_CAPTURE_GIF_BYTES:
        raise RenderError("live capture GIF exceeds size limit")
    gif_probe = ffprobe(asset)
    if not math.isclose(
        float(gif_probe["format"]["duration"]),
        float(playback["durationSeconds"]),
        abs_tol=0.05,
    ):
        raise RenderError("live capture GIF duration differs from receipt")

    sums_path = resolve_repo_regular_file(
        receipt_relative_dir / "SHA256SUMS",
        field="live capture SHA256SUMS",
    )
    validate_sha256sums(
        sums_path,
        expected_paths={**file_paths, "receipt.json": receipt_path},
    )
    capture_paths.extend([receipt_path, sums_path])
    return asset, receipt_path, capture_paths, receipt


def font_candidates(bold: bool) -> list[str]:
    if bold:
        return [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    return [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ]


def load_font(size: int, *, bold: bool = False):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise RenderError(
            "Pillow is required. Install demo/contest-video/requirements.txt"
        ) from exc
    errors: list[str] = []
    for candidate in font_candidates(bold):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    raise RenderError("no usable TrueType font found:\n" + "\n".join(errors))


def load_mono_font(size: int):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise RenderError(
            "Pillow is required. Install demo/contest-video/requirements.txt"
        ) from exc
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "DejaVuSansMono.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def terminal_colour(line: str) -> tuple[int, int, int]:
    stripped = line.strip()
    if stripped.startswith("MISMATCH") or "exit 1" in stripped:
        return (248, 113, 113)
    if stripped.startswith("MATCH") or "exit 0" in stripped:
        return (74, 222, 128)
    if stripped.startswith("$") or stripped.startswith("=="):
        return (34, 211, 238)
    if (
        stripped.startswith("#")
        or stripped.startswith("L1")
        or stripped.startswith("L2")
        or stripped.startswith("L3")
        or stripped.startswith("  L")
    ):
        return (148, 163, 184)
    if stripped.startswith("verdict:"):
        return (251, 191, 36)
    return (226, 232, 240)


def wrap_terminal_lines(text: str, columns: int = 118) -> list[str]:
    wrapped: list[str] = []
    for original in text.splitlines():
        if not original:
            wrapped.append("")
            continue
        remaining = original
        while len(remaining) > columns:
            split_at = remaining.rfind(" ", 0, columns + 1)
            if split_at < columns // 2:
                split_at = columns
            wrapped.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        wrapped.append(remaining)
    return wrapped


def render_terminal_capture_gif(
    transcript: Path,
    output: Path,
    *,
    target_seconds: float,
) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RenderError(
            "Pillow is required. Install demo/contest-video/requirements.txt"
        ) from exc
    lines = wrap_terminal_lines(transcript.read_text(encoding="utf-8"))
    if not lines:
        raise RenderError("captured terminal transcript is empty")

    width, height = 1600, 900
    header_height = 82
    padding = 34
    line_height = 27
    font = load_mono_font(21)
    header_font = load_font(26, bold=True)
    note_font = load_font(18)
    visible_lines = (height - header_height - padding * 2) // line_height
    frames = []
    raw_durations: list[int] = []

    for shown in range(1, len(lines) + 1):
        image = Image.new("RGB", (width, height), (7, 13, 22))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, header_height), fill=(10, 23, 40))
        draw.ellipse((26, 29, 42, 45), fill=(248, 113, 113))
        draw.ellipse((50, 29, 66, 45), fill=(251, 191, 36))
        draw.ellipse((74, 29, 90, 45), fill=(74, 222, 128))
        draw.text(
            (116, 21),
            "ACTUAL LOCAL RUN  •  make demo",
            font=header_font,
            fill=(226, 232, 240),
        )
        draw.text(
            (1570, 28),
            "literal PTY output • timing normalized",
            font=note_font,
            fill=(148, 163, 184),
            anchor="ra",
        )
        start = max(0, shown - visible_lines)
        visible = lines[start:shown]
        for row, line in enumerate(visible):
            y = header_height + padding + row * line_height
            draw.text((padding, y), line, font=font, fill=terminal_colour(line))
        newest = visible[-1]
        cursor_x = padding + min(font.getlength(newest), width - padding * 2 - 12)
        cursor_y = header_height + padding + (len(visible) - 1) * line_height
        draw.rectangle(
            (cursor_x, cursor_y + 2, cursor_x + 10, cursor_y + line_height - 3),
            fill=(34, 211, 238),
        )
        frames.append(image)
        stripped = lines[shown - 1].strip()
        if stripped.startswith(("MISMATCH", "MATCH")):
            raw_durations.append(1150)
        elif stripped.startswith("PASS:"):
            raw_durations.append(850)
        elif stripped.startswith("verdict:"):
            raw_durations.append(650)
        else:
            raw_durations.append(170)

    final_hold_ms = 4000
    reveal_budget_ms = int(round(target_seconds * 1000)) - final_hold_ms
    scale = reveal_budget_ms / sum(raw_durations)
    durations = [max(50, int(round(value * scale / 10) * 10)) for value in raw_durations]
    durations.append(final_hold_ms)
    frames.append(frames[-1].copy())
    difference = int(round(target_seconds * 1000)) - sum(durations)
    durations[-1] += difference
    if durations[-1] <= 0:
        raise RenderError("terminal capture timing could not fit the requested footage duration")

    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


def text_width(draw, text: str, font) -> float:
    box = draw.textbbox((0, 0), text, font=font)
    return float(box[2] - box[0])


def wrap_pixels(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and text_width(draw, candidate, font) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def fit_font(draw, text: str, *, max_width: int, start: int, minimum: int, bold: bool):
    for size in range(start, minimum - 1, -2):
        font = load_font(size, bold=bold)
        if text_width(draw, text, font) <= max_width:
            return font
    return load_font(minimum, bold=bold)


def render_slide(
    scene: dict[str, Any],
    *,
    index: int,
    count: int,
    elapsed: float,
    total: float,
    output: Path,
) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RenderError(
            "Pillow is required. Install demo/contest-video/requirements.txt"
        ) from exc

    width, height = 1920, 1080
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    accent = COLORS[scene["accent"]]
    for y in range(height):
        vertical = y / (height - 1)
        for x in range(width):
            horizontal = x / (width - 1)
            glow = max(0.0, 1.0 - math.hypot(horizontal - 0.12, vertical - 0.15) / 0.75)
            pixels[x, y] = (
                int(8 + 7 * glow),
                int(15 + 18 * glow),
                int(29 + 31 * glow),
            )

    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, 14, height), fill=(*accent, 255))
    draw.rounded_rectangle((78, 66, 1842, 1006), radius=32, fill=(6, 12, 25, 220))
    draw.rounded_rectangle((96, 224, 1824, 510), radius=26, fill=(13, 25, 46, 238))
    draw.rectangle((96, 224, 106, 510), fill=(*accent, 255))

    label_font = load_font(25, bold=True)
    title_font = fit_font(
        draw,
        scene["title"],
        max_width=1450,
        start=58,
        minimum=40,
        bold=True,
    )
    hero_font = fit_font(
        draw,
        scene["hero"],
        max_width=1580,
        start=84,
        minimum=54,
        bold=True,
    )
    subhero_font = fit_font(
        draw,
        scene["subhero"],
        max_width=1580,
        start=38,
        minimum=28,
        bold=False,
    )
    bullet_font = load_font(30)
    source_font = load_font(21)
    meta_font = load_font(23, bold=True)

    draw.text((105, 91), "POLYGRAPH", font=meta_font, fill=(226, 232, 240, 255))
    draw.text(
        (1810, 91),
        f"{index + 1:02d} / {count:02d}",
        font=meta_font,
        fill=(148, 163, 184, 255),
        anchor="ra",
    )
    draw.text((105, 142), scene["kicker"], font=label_font, fill=(*accent, 255))
    draw.text((105, 176), scene["title"], font=title_font, fill=(248, 250, 252, 255))
    draw.text((145, 275), scene["hero"], font=hero_font, fill=(*accent, 255))
    draw.text((145, 405), scene["subhero"], font=subhero_font, fill=(226, 232, 240, 255))

    y = 565
    for bullet in scene["bullets"]:
        lines = wrap_pixels(draw, bullet, bullet_font, 1520)
        draw.ellipse((116, y + 10, 130, y + 24), fill=(*accent, 255))
        for line_index, line in enumerate(lines):
            draw.text(
                (154, y + line_index * 42),
                line,
                font=bullet_font,
                fill=(226, 232, 240, 255),
            )
        y += max(58, len(lines) * 42 + 18)

    source_text = "Sources: " + " • ".join(scene["sources"])
    source_lines = wrap_pixels(draw, source_text, source_font, 1640)
    source_y = 925 - (len(source_lines) - 1) * 29
    for line in source_lines:
        draw.text((105, source_y), line, font=source_font, fill=(148, 163, 184, 255))
        source_y += 29

    progress_start = elapsed / total
    progress_end = (elapsed + float(scene["duration_seconds"])) / total
    draw.rounded_rectangle((105, 967, 1815, 979), radius=6, fill=(30, 41, 59, 255))
    draw.rounded_rectangle(
        (105, 967, 105 + int(1710 * progress_end), 979),
        radius=6,
        fill=(*accent, 255),
    )
    draw.text(
        (1815, 993),
        f"{int(round(progress_start * total)):02d}s → {int(round(progress_end * total)):02d}s"
        f" / {int(total)}s",
        font=source_font,
        fill=(100, 116, 139, 255),
        anchor="ra",
    )
    image.save(output, format="PNG", optimize=True)


def ffprobe(path: Path) -> dict[str, Any]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=filename,duration,size,bit_rate:"
                "stream=index,codec_type,codec_name,profile,pix_fmt,width,height,"
                "r_frame_rate,sample_rate,channels"
            ),
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    return json.loads(result.stdout)


def audio_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def render_narration(
    text: str,
    output: Path,
    *,
    voice: str,
    rate: int,
) -> None:
    command = ["say", "-r", str(rate), "-o", str(output)]
    if voice:
        command[1:1] = ["-v", voice]
    command.append(text)
    run(command)


def render_segment(
    slide: Path,
    audio: Path | None,
    output: Path,
    *,
    duration: float,
    fps: int,
) -> None:
    fade_out = max(0.0, duration - 0.35)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(slide),
    ]
    if audio is not None:
        command += ["-i", str(audio)]
        command += [
            "-filter_complex",
            (
                f"[0:v]fade=t=in:st=0:d=0.35,"
                f"fade=t=out:st={fade_out:.3f}:d=0.35,format=yuv420p[v];"
                f"[1:a]aresample=48000,apad=pad_dur={duration:.3f}[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
        ]
    else:
        command += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            (
                f"[0:v]fade=t=in:st=0:d=0.35,"
                f"fade=t=out:st={fade_out:.3f}:d=0.35,format=yuv420p[v]"
            ),
            "-map",
            "[v]",
            "-map",
            "1:a:0",
        ]
    command += [
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(command)


def render_hybrid_segment(
    slide: Path,
    live_capture: Path,
    audio: Path | None,
    output: Path,
    *,
    duration: float,
    static_seconds: float,
    footage_seconds: float,
    fps: int,
) -> None:
    if not math.isclose(static_seconds + footage_seconds, duration, abs_tol=0.001):
        raise RenderError("hybrid segment timings do not add up to the scene duration")
    fade_out = max(0.0, duration - 0.35)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(slide),
        "-ignore_loop",
        "1",
        "-i",
        str(live_capture),
    ]
    if audio is not None:
        command += ["-i", str(audio)]
        audio_filter = (
            f"[2:a]aresample=48000,apad=pad_dur={duration:.3f}[a]"
        )
        audio_map = "[a]"
    else:
        command += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
        ]
        audio_filter = "[2:a]anull[a]"
        audio_map = "[a]"

    filter_complex = (
        f"[0:v]trim=duration={static_seconds:.3f},setpts=PTS-STARTPTS,"
        f"fps={fps},scale=1920:1080,setsar=1,format=yuv420p[card];"
        f"[1:v]fps={fps},scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x07101e,"
        f"tpad=stop_mode=clone:stop_duration={footage_seconds:.3f},"
        f"trim=duration={footage_seconds:.3f},setpts=PTS-STARTPTS,"
        f"setsar=1,format=yuv420p[live];"
        f"[card][live]concat=n=2:v=1:a=0,"
        f"fade=t=in:st=0:d=0.35,"
        f"fade=t=out:st={fade_out:.3f}:d=0.35,format=yuv420p[v];"
        f"{audio_filter}"
    )
    command += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        audio_map,
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(command)


def concat_segments(segments: list[Path], concat_file: Path, output: Path) -> None:
    lines = []
    for segment in segments:
        escaped = str(segment.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def render_qa_contact_sheet(output: Path, story: dict[str, Any]) -> list[Path]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RenderError(
            "Pillow is required. Install demo/contest-video/requirements.txt"
        ) from exc
    qa_dir = resolve_repo_directory(
        output.parent.relative_to(REPO_ROOT_RESOLVED) / "qa",
        field="QA directory",
        create=True,
    )
    elapsed = 0.0
    frame_paths: list[Path] = []
    for index, scene in enumerate(story["scenes"], start=1):
        timestamp = elapsed + float(scene["duration_seconds"]) / 2
        frame = resolve_repo_output_file(
            qa_dir.relative_to(REPO_ROOT_RESOLVED) / f"{index:02d}.png",
            field=f"QA frame {index:02d}",
        )
        run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(output),
                "-frames:v",
                "1",
                str(frame),
            ]
        )
        frame_paths.append(frame)
        elapsed += float(scene["duration_seconds"])

    contact_sheet = resolve_repo_output_file(
        qa_dir.relative_to(REPO_ROOT_RESOLVED) / "contact-sheet.jpg",
        field="QA contact sheet",
    )
    sheet = Image.new("RGB", (1280, 1080), "#020617")
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frame_paths):
        image = Image.open(frame).convert("RGB")
        image.thumbnail((640, 360))
        x = (index % 2) * 640
        y = (index // 2) * 360
        sheet.paste(image, (x, y))
        draw.rounded_rectangle(
            (x + 8, y + 8, x + 72, y + 38),
            radius=6,
            fill="#020617",
        )
        draw.text((x + 18, y + 14), frame.stem, fill="white")
    sheet.save(contact_sheet, quality=92)
    return [*frame_paths, contact_sheet]


def srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(story: dict[str, Any], path: Path) -> None:
    elapsed = 0.0
    blocks: list[str] = []
    for index, scene in enumerate(story["scenes"], start=1):
        end = elapsed + float(scene["duration_seconds"])
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{srt_timestamp(elapsed)} --> {srt_timestamp(end)}",
                    scene["narration"],
                ]
            )
        )
        elapsed = end
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def validate_media(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RenderError(f"rendered MP4 does not exist: {path}")
    probe = ffprobe(path)
    duration = float(probe["format"]["duration"])
    size_bytes = int(probe["format"]["size"])
    video = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    errors: list[str] = []
    if duration >= MAX_DURATION_SECONDS:
        errors.append(f"duration {duration:.3f}s is not under {MAX_DURATION_SECONDS:.0f}s")
    if size_bytes > MAX_FINAL_MP4_BYTES:
        errors.append(
            f"MP4 size {size_bytes} exceeds {MAX_FINAL_MP4_BYTES} byte limit"
        )
    if len(video) != 1:
        errors.append(f"expected one video stream, found {len(video)}")
    if len(audio) != 1:
        errors.append(f"expected one audio stream, found {len(audio)}")
    if video:
        stream = video[0]
        if stream.get("codec_name") != "h264":
            errors.append(f"video codec is {stream.get('codec_name')}, expected h264")
        if stream.get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
            errors.append(f"pixel format is {stream.get('pix_fmt')}, expected 4:2:0")
        if (stream.get("width"), stream.get("height")) != (1920, 1080):
            errors.append(
                f"frame is {stream.get('width')}x{stream.get('height')}, expected 1920x1080"
            )
        if stream.get("r_frame_rate") != "30/1":
            errors.append(f"frame rate is {stream.get('r_frame_rate')}, expected 30/1")
    if audio:
        stream = audio[0]
        if stream.get("codec_name") != "aac":
            errors.append(f"audio codec is {stream.get('codec_name')}, expected aac")
        if stream.get("sample_rate") != "48000":
            errors.append(f"audio sample rate is {stream.get('sample_rate')}, expected 48000")
        if int(stream.get("channels", 0)) != 2:
            errors.append(f"audio channels is {stream.get('channels')}, expected 2")

    prefix = path.read_bytes()[: 4 * 1024 * 1024]
    moov_offset = prefix.find(b"moov")
    mdat_offset = prefix.find(b"mdat")
    faststart = moov_offset >= 0 and mdat_offset >= 0 and moov_offset < mdat_offset
    if not faststart:
        errors.append("MP4 is not fast-started (moov atom not before mdat)")
    if errors:
        raise RenderError("media validation failed:\n- " + "\n- ".join(errors))
    return {
        "durationSeconds": duration,
        "under180Seconds": True,
        "video": video[0],
        "audio": audio[0],
        "fastStart": faststart,
        "sizeBytes": size_bytes,
        "bitRate": int(probe["format"]["bit_rate"]),
    }


def write_validation_receipt(
    *,
    output: Path,
    story_path: Path,
    evidence_paths: list[Path],
    media: dict[str, Any],
    tts_mode: str,
    voice: str,
    rate: int,
    live_capture_receipt: dict[str, Any],
    qa_paths: list[Path],
) -> Path:
    receipt_path = resolve_repo_output_file(
        output.with_suffix(".validation.json").relative_to(REPO_ROOT_RESOLVED),
        field="video validation receipt",
    )
    receipt = {
        "schema": "polygraph.contest-video.validation.v1",
        "output": str(output.relative_to(REPO_ROOT_RESOLVED)),
        "sha256": sha256(output),
        "story": str(story_path.relative_to(REPO_ROOT)),
        "storySha256": sha256(story_path),
        "generator": {
            "path": str(RENDERER_PATH),
            "sha256": sha256(REPO_ROOT_RESOLVED / RENDERER_PATH),
        },
        "ttsMode": tts_mode,
        "voice": voice if tts_mode == "say" else None,
        "rateWordsPerMinute": rate if tts_mode == "say" else None,
        "media": media,
        "liveCapture": live_capture_receipt,
        "qa": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": sha256(path),
            }
            for path in qa_paths
        ],
        "evidence": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": sha256(path),
            }
            for path in evidence_paths
        ],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    sha_path = resolve_repo_output_file(
        output.with_suffix(".sha256").relative_to(REPO_ROOT_RESOLVED),
        field="video SHA256",
    )
    sha_path.write_text(
        f"{receipt['sha256']}  {output.name}\n", encoding="utf-8"
    )
    return receipt_path


def format_clock(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)}:{remainder:06.3f}"


def update_production_record_from_receipt(receipt_path: Path) -> None:
    """Replace the production record's generated block from the final sidecar."""

    receipt = require_exact_keys(
        load_json(receipt_path),
        {
            "schema",
            "output",
            "sha256",
            "story",
            "storySha256",
            "generator",
            "ttsMode",
            "voice",
            "rateWordsPerMinute",
            "media",
            "liveCapture",
            "qa",
            "evidence",
        },
        field="final validation receipt",
    )
    if receipt["schema"] != "polygraph.contest-video.validation.v1":
        raise RenderError("final validation receipt schema is missing or unsupported")
    output = resolve_repo_regular_file(
        receipt["output"],
        field="final validation receipt output",
    )
    media = require_exact_keys(
        receipt["media"],
        {
            "durationSeconds",
            "under180Seconds",
            "video",
            "audio",
            "fastStart",
            "sizeBytes",
            "bitRate",
        },
        field="final validation receipt media",
    )
    generator = require_exact_keys(
        receipt["generator"],
        {"path", "sha256"},
        field="final validation receipt generator",
    )
    if generator["path"] != str(RENDERER_PATH):
        raise RenderError("final validation receipt generator path changed")
    generator_path = resolve_repo_regular_file(
        generator["path"],
        field="final validation receipt generator file",
    )
    if generator["sha256"] != sha256(generator_path):
        raise RenderError("final validation receipt generator hash is stale")
    if receipt["sha256"] != sha256(output):
        raise RenderError("final validation receipt MP4 hash is stale")
    if int(media["sizeBytes"]) != output.stat().st_size:
        raise RenderError("final validation receipt MP4 size is stale")
    if media["under180Seconds"] is not True or media["fastStart"] is not True:
        raise RenderError("final validation receipt does not record a valid web MP4")

    duration = float(media["durationSeconds"])
    if duration >= MAX_DURATION_SECONDS:
        raise RenderError("final validation receipt duration is not under 180 seconds")
    margin = MAX_DURATION_SECONDS - duration
    video = media["video"]
    audio = media["audio"]
    live = receipt["liveCapture"]
    live_source = live["source"]
    live_run = live["run"]
    live_playback = live["playback"]
    evidence_hashes = {
        item["path"]: item["sha256"]
        for item in receipt["evidence"]
        if isinstance(item, dict)
        and set(item) == {"path", "sha256"}
        and isinstance(item["path"], str)
        and isinstance(item["sha256"], str)
    }
    live_receipt_relative = "demo/contest-video/live-run/receipt.json"
    live_receipt_hash = evidence_hashes.get(live_receipt_relative)
    if not live_receipt_hash:
        raise RenderError("final validation receipt does not bind the live capture receipt")

    block = "\n".join(
        [
            PRODUCTION_RECORD_BEGIN,
            "### Authoritative rendered submission cut",
            "",
            "<!-- Generated by tools/render_contest_video.py from the final validation sidecar. Do not hand-edit values in this block. -->",
            "",
            f"Final sidecar: `{receipt_path.relative_to(REPO_ROOT_RESOLVED)}`",
            "",
            "| Property | Final post-hardening value |",
            "|---|---|",
            f"| Repository path | `{receipt['output']}` |",
            f"| Exact local path | `{output}` |",
            f"| Encoded duration | {duration:.6f} s ({format_clock(duration)}) |",
            f"| Margin under 180 s | {margin:.6f} s |",
            f"| Frame | {video['width']}×{video['height']} at {video['r_frame_rate']} fps |",
            f"| Video | H.264 {video.get('profile', 'unknown')}, `{video['pix_fmt']}` |",
            f"| Audio | AAC-{audio.get('profile', 'unknown')}, {audio['sample_rate']} Hz, {audio['channels']} channels |",
            f"| Streaming | fast-start: `{str(bool(media['fastStart'])).lower()}` |",
            f"| Size | {int(media['sizeBytes']):,} bytes |",
            f"| SHA-256 | `{receipt['sha256']}` |",
            "",
            "| Real project-function capture | Bound value |",
            "|---|---|",
            "| Command | `make demo` |",
            f"| Reviewed source | `{live_source['branch']}` at `{live_source['gitHead']}`; `git archive HEAD`, working-tree edits excluded |",
            f"| Actual run | {float(live_run['wallSeconds']):.6f} s wall time; overall exit {live_run['exitCode']}; {live_run['ptyEventCount']} PTY events |",
            f"| In final cut | 9.000 s evidence card + {float(live_playback['durationSeconds']):.3f} s literal-output playback |",
            f"| Playback timing | {live_playback['timing']} |",
            f"| Capture receipt | `{live_receipt_relative}`; SHA-256 `{live_receipt_hash}` |",
            f"| Raw transcript SHA-256 | `{live['files']['make-demo.ansi']}` |",
            f"| Event stream SHA-256 | `{live['files']['make-demo.events.jsonl']}` |",
            f"| Playback GIF SHA-256 | `{live['files']['make-demo.gif']}` |",
            PRODUCTION_RECORD_END,
        ]
    )

    record_path = resolve_repo_regular_file(
        PRODUCTION_RECORD_PATH,
        field="production record",
    )
    text = record_path.read_text(encoding="utf-8")
    if text.count(PRODUCTION_RECORD_BEGIN) != 1 or text.count(PRODUCTION_RECORD_END) != 1:
        raise RenderError(
            "production record must contain exactly one authoritative receipt marker pair"
        )
    start = text.index(PRODUCTION_RECORD_BEGIN)
    end = text.index(PRODUCTION_RECORD_END, start) + len(PRODUCTION_RECORD_END)
    updated = text[:start] + block + text[end:]
    record_path.write_text(updated, encoding="utf-8")
    print(f"production record synced from validation sidecar: {record_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", type=Path, default=DEFAULT_STORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--tts",
        choices=("auto", "say", "none"),
        default="auto",
        help="auto uses macOS say when available; none renders a silent AAC track",
    )
    parser.add_argument("--voice", default="Samantha")
    parser.add_argument("--rate", type=int, default=190)
    parser.add_argument(
        "--capture-demo",
        action="store_true",
        help="capture a fresh real make demo run before rendering",
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="capture and validate the real make demo run, then exit",
    )
    parser.add_argument(
        "--capture-dir",
        type=Path,
        default=DEFAULT_CAPTURE_DIR,
        help="where the raw terminal transcript, playback, receipt, and hashes live",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run path-containment, symlink, and receipt-schema regression selftests",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument(
        "--validate-only",
        type=Path,
        help="validate an existing MP4 and exit without rendering",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rate < 120 or args.rate > 260:
        raise RenderError("--rate must be between 120 and 260 words per minute")
    for command in ("ffmpeg", "ffprobe"):
        if shutil.which(command) is None:
            raise RenderError(f"required command not found on PATH: {command}")

    if args.self_test:
        run_security_selftests()
        return 0

    capture_dir = resolve_repo_directory(
        args.capture_dir,
        field="capture directory",
        create=True,
    )
    captured: tuple[Path, list[Path], dict[str, Any]] | None = None
    if args.capture_demo or args.capture_only:
        captured = capture_make_demo(capture_dir)

    facts, evidence_paths = load_facts()
    story_path = resolve_repo_regular_file(args.story, field="story")
    if story_path.stat().st_size > MAX_STORY_BYTES:
        raise RenderError(f"story exceeds {MAX_STORY_BYTES} byte limit")
    story = load_story(story_path, facts)
    if captured is None:
        live_asset, live_receipt_path, capture_paths, live_receipt = resolve_live_capture(
            story
        )
    else:
        live_receipt_path, capture_paths, live_receipt = captured
        live_asset = REPO_ROOT / live_receipt["playback"]["asset"]
        expected_receipt = REPO_ROOT / next(
            scene["live_capture"]["receipt"]
            for scene in story["scenes"]
            if scene.get("live_capture")
        )
        if live_receipt_path.resolve() != expected_receipt.resolve():
            raise RenderError(
                f"captured receipt {live_receipt_path} does not match story {expected_receipt}"
            )
        # Re-read and verify hashes through the same path used for cached captures.
        live_asset, live_receipt_path, capture_paths, live_receipt = resolve_live_capture(
            story
        )
    evidence_paths.extend(capture_paths)
    target_duration = float(story["target_duration_seconds"])
    print(
        f"evidence OK; {len(story['scenes'])} scenes, "
        f"{target_duration:.1f}s target (< {MAX_DURATION_SECONDS:.0f}s); "
        f"real footage={next(scene['live_capture']['footage_seconds'] for scene in story['scenes'] if scene.get('live_capture'))}s"
    )

    if args.capture_only:
        print(f"capture receipt validated: {live_receipt_path}")
        return 0
    if args.validate_only:
        validate_path = resolve_repo_regular_file(
            args.validate_only,
            field="validate-only MP4",
        )
        media = validate_media(validate_path)
        print(json.dumps(media, indent=2))
        return 0
    if args.dry_run:
        return 0

    output = resolve_repo_output_file(args.output, field="output MP4")
    tts_mode = args.tts
    if tts_mode == "auto":
        tts_mode = "say" if shutil.which("say") else "none"
    if tts_mode == "say" and shutil.which("say") is None:
        raise RenderError("--tts say requested, but macOS say is not on PATH")

    work_parent = output.parent
    if args.keep_work:
        work = Path(tempfile.mkdtemp(prefix="polygraph-video-work-", dir=work_parent))
        cleanup = False
    else:
        temporary = tempfile.TemporaryDirectory(
            prefix="polygraph-video-work-", dir=work_parent
        )
        work = Path(temporary.name)
        cleanup = True

    try:
        prepared: list[tuple[dict[str, Any], Path, Path | None]] = []
        narration_overages: list[str] = []
        elapsed = 0.0
        for index, scene in enumerate(story["scenes"]):
            scene_id = scene["id"]
            duration = float(scene["duration_seconds"])
            slide = work / f"{scene_id}.png"
            render_slide(
                scene,
                index=index,
                count=len(story["scenes"]),
                elapsed=elapsed,
                total=target_duration,
                output=slide,
            )
            narration: Path | None = None
            if tts_mode == "say":
                narration = work / f"{scene_id}.aiff"
                render_narration(
                    scene["narration"],
                    narration,
                    voice=args.voice,
                    rate=args.rate,
                )
                spoken = audio_duration(narration)
                if spoken > duration - 0.75:
                    narration_overages.append(
                        f"{scene_id} narration is {spoken:.2f}s but the scene is "
                        f"{duration:.2f}s; increase --rate or edit the narration"
                    )
            prepared.append((scene, slide, narration))
            elapsed += duration

        if narration_overages:
            raise RenderError(
                "narration preflight failed:\n- " + "\n- ".join(narration_overages)
            )

        segments: list[Path] = []
        for scene, slide, narration in prepared:
            scene_id = scene["id"]
            duration = float(scene["duration_seconds"])
            segment = work / f"{scene_id}.mp4"
            if scene.get("live_capture"):
                live = scene["live_capture"]
                render_hybrid_segment(
                    slide,
                    live_asset,
                    narration,
                    segment,
                    duration=duration,
                    static_seconds=float(live["static_seconds"]),
                    footage_seconds=float(live["footage_seconds"]),
                    fps=int(story["format"]["fps"]),
                )
            else:
                render_segment(
                    slide,
                    narration,
                    segment,
                    duration=duration,
                    fps=int(story["format"]["fps"]),
                )
            segments.append(segment)

        concat_segments(segments, work / "concat.txt", output)
        srt_path = resolve_repo_output_file(
            output.with_suffix(".srt").relative_to(REPO_ROOT_RESOLVED),
            field="video SRT",
        )
        write_srt(story, srt_path)
        if srt_path.stat().st_size > MAX_CLEAN_TRANSCRIPT_BYTES:
            raise RenderError("video SRT exceeds size limit")
        media = validate_media(output)
        qa_paths = render_qa_contact_sheet(output, story)
        receipt_path = write_validation_receipt(
            output=output,
            story_path=story_path,
            evidence_paths=evidence_paths,
            media=media,
            tts_mode=tts_mode,
            voice=args.voice,
            rate=args.rate,
            live_capture_receipt=live_receipt,
            qa_paths=qa_paths,
        )
        update_production_record_from_receipt(receipt_path)
        print(f"rendered: {output}")
        print(f"captions: {srt_path}")
        print(f"validation: {receipt_path}")
        print(f"duration: {media['durationSeconds']:.3f}s")
        print(f"size: {media['sizeBytes']} bytes")
        print(f"sha256: {sha256(output)}")
        return 0
    finally:
        if cleanup:
            temporary.cleanup()
        else:
            print(f"kept render work directory: {work}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RenderError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
