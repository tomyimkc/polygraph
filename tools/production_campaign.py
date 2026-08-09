#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed, production-shaped evidence campaign for Arm64 llama-server.

This orchestrator builds on ``tools/server_readiness.py``.  It runs paired
baseline/candidate readiness measurements, replays a deterministic synthetic
tenant trace, injects controlled load-generator delay/errors, performs
process-and-model reload restarts, checks synthetic tenant sentinels for
cross-trace leakage, evaluates explicit SLOs, and emits checksummed receipts.

It never consumes actual production traffic or customer data.  Its receipts
always carry the exact boundary flags:

``actualProductionTraffic:false``
``productionReady:false``
``candidateOnly:true``

Exit codes:
  0  paired evidence completed and the candidate comparison gate passed
  1  measurement completed but the candidate must be rolled back or held
  2  infrastructure/configuration prevented a valid paired measurement
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import http.client
import importlib.util
import json
import math
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


SCHEMA = "polygraph.production-campaign.v1"
CONFIG_SCHEMA = "polygraph.production-readiness-config.v1"
TRACE_SCHEMA = "polygraph.production-campaign.synthetic-trace.v1"
REPLAY_RESULT_SCHEMA = "polygraph.production-campaign.replay-result.v1"
CLAIM_FLAGS = {
    "actualProductionTraffic": False,
    "productionReady": False,
    "candidateOnly": True,
}
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "production-readiness.json"
DEFAULT_MODEL_MANIFEST = Path(__file__).resolve().parents[1] / "scripts" / "models.txt"
ALLOWED_MODEL_LICENSES = {"apache-2.0", "mit"}
REVIEWED_LLAMA_CPP_SHA = "dbadb68eecdfb3ab0e86872d011738fc937f0364"

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_TENANT_RE = re.compile(r"^synthetic-tenant-[a-z0-9-]+$")
_SENTINEL_RE = re.compile(r"^PGSYNTH-[A-F0-9]{20}$")


def _load_server_readiness() -> Any:
    """Load the sibling module without requiring tools/ to be a package."""
    name = "polygraph_server_readiness"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("server_readiness.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load readiness harness: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


readiness = _load_server_readiness()


class ConfigurationError(ValueError):
    """Raised when a campaign configuration is incomplete or unsafe."""


@dataclasses.dataclass(frozen=True)
class TrafficItem:
    schema: str
    campaignShard: int
    repetition: int
    sequence: int
    requestId: str
    tenantLabel: str
    sentinel: str
    prompt: str
    promptSha256: str
    containsPii: bool
    injectedDelayMs: int
    injectLoadGeneratorError: bool


@dataclasses.dataclass
class CompletionObservation:
    status: str
    httpStatus: int | None
    text: str
    ttftMs: float | None
    e2eMs: float
    promptTokens: int | None
    completionTokens: int | None
    error: str | None


def utc_now() -> str:
    return readiness.utc_now()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_manifest_entry(manifest_path: Path, model_id: str) -> dict[str, Any]:
    """Resolve one pinned, redistributable model row from scripts/models.txt."""
    if not re.fullmatch(r"[a-z0-9._-]+", model_id):
        raise ConfigurationError("model id must match [a-z0-9._-]+")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ConfigurationError(
            f"model manifest must be a regular non-symlink file: {manifest_path}"
        )
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigurationError(
            f"cannot read model manifest {manifest_path}: {exc}"
        ) from exc

    matches: list[tuple[int, list[str]]] = []
    for line_number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [field.strip() for field in raw.split("|")]
        if len(fields) < 5:
            raise ConfigurationError(
                f"model manifest line {line_number} has fewer than five fields"
            )
        if fields[0] == model_id:
            matches.append((line_number, fields))
    if not matches:
        raise ConfigurationError(f"model id {model_id!r} not found in {manifest_path}")
    if len(matches) != 1:
        raise ConfigurationError(
            f"model id {model_id!r} appears more than once in {manifest_path}"
        )

    line_number, fields = matches[0]
    resolved_id, hf_repo, hf_file, expected_sha256, license_name = fields[:5]
    license_name = license_name.lower()
    if resolved_id != model_id:
        raise ConfigurationError("resolved model id mismatch")
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", hf_repo):
        raise ConfigurationError(
            f"model manifest line {line_number} has invalid hf_repo"
        )
    if (
        not hf_file
        or Path(hf_file).name != hf_file
        or "/" in hf_file
        or "\\" in hf_file
    ):
        raise ConfigurationError(
            f"model manifest line {line_number} has unsafe hf_file"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ConfigurationError(
            f"model manifest line {line_number} has invalid sha256"
        )
    if license_name not in ALLOWED_MODEL_LICENSES:
        raise ConfigurationError(
            f"model {model_id!r} license {license_name!r} is not allowed; "
            "expected apache-2.0 or mit"
        )
    return {
        "modelId": model_id,
        "hfRepo": hf_repo,
        "hfFile": hf_file,
        "sha256": expected_sha256,
        "license": license_name,
        "manifestPath": str(manifest_path),
        "manifestSha256": sha256_file(manifest_path),
        "manifestLine": line_number,
    }


def validate_reviewed_llama_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ConfigurationError(
            "llama.cpp provenance must be an exact lowercase 40-hex commit"
        )
    if value != REVIEWED_LLAMA_CPP_SHA:
        raise ConfigurationError(
            f"llama.cpp commit is not allowlisted: {value}; "
            f"expected {REVIEWED_LLAMA_CPP_SHA}"
        )
    return value


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def _require_number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be numeric")
    if not math.isfinite(float(value)):
        raise ConfigurationError(f"{name} must be finite")
    if integer and not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer")
    if minimum is not None and float(value) < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    if maximum is not None and float(value) > maximum:
        raise ConfigurationError(f"{name} must be <= {maximum}")
    return value


def _require_keys(obj: dict[str, Any], name: str, keys: Iterable[str]) -> None:
    missing = sorted(set(keys) - set(obj))
    if missing:
        raise ConfigurationError(f"{name} missing required keys: {', '.join(missing)}")


def _reject_unknown_keys(
    obj: dict[str, Any], name: str, allowed: Iterable[str]
) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ConfigurationError(f"{name} has unknown keys: {', '.join(unknown)}")


def load_config(
    path: Path,
    profile_name: str,
    *,
    repetitions_override: int | None = None,
    soak_seconds_override: float | None = None,
) -> dict[str, Any]:
    """Load and strictly validate the explicit campaign/SLO configuration."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc

    root = _require_dict(raw, "config")
    root_keys = {
        "schema",
        "claims",
        "syntheticTraffic",
        "sloProfiles",
        "profiles",
    }
    _require_keys(root, "config", root_keys)
    _reject_unknown_keys(root, "config", root_keys)
    if root["schema"] != CONFIG_SCHEMA:
        raise ConfigurationError(
            f"config schema must be {CONFIG_SCHEMA!r}, got {root['schema']!r}"
        )

    claims = _require_dict(root["claims"], "claims")
    _require_keys(claims, "claims", CLAIM_FLAGS)
    _reject_unknown_keys(claims, "claims", CLAIM_FLAGS)
    for key, expected in CLAIM_FLAGS.items():
        observed = _require_bool(claims[key], f"claims.{key}")
        if observed is not expected:
            raise ConfigurationError(
                f"claims.{key} must remain exactly {str(expected).lower()}"
            )

    traffic = _require_dict(root["syntheticTraffic"], "syntheticTraffic")
    traffic_keys = {
        "seed",
        "tenantLabels",
        "delayEveryRequests",
        "delayMilliseconds",
        "errorEveryRequests",
        "errorHttpStatus",
    }
    _require_keys(traffic, "syntheticTraffic", traffic_keys)
    _reject_unknown_keys(traffic, "syntheticTraffic", traffic_keys)
    _require_number(traffic["seed"], "syntheticTraffic.seed", minimum=0, integer=True)
    labels = traffic["tenantLabels"]
    if not isinstance(labels, list) or len(labels) < 2:
        raise ConfigurationError(
            "syntheticTraffic.tenantLabels must contain at least two labels"
        )
    if len(labels) != len(set(labels)):
        raise ConfigurationError("syntheticTraffic.tenantLabels must be unique")
    for label in labels:
        if not isinstance(label, str) or not _TENANT_RE.fullmatch(label):
            raise ConfigurationError(
                "tenant labels must match synthetic-tenant-[a-z0-9-]+"
            )
    _require_number(
        traffic["delayEveryRequests"],
        "syntheticTraffic.delayEveryRequests",
        minimum=0,
        integer=True,
    )
    _require_number(
        traffic["delayMilliseconds"],
        "syntheticTraffic.delayMilliseconds",
        minimum=0,
        integer=True,
    )
    _require_number(
        traffic["errorEveryRequests"],
        "syntheticTraffic.errorEveryRequests",
        minimum=0,
        integer=True,
    )
    _require_number(
        traffic["errorHttpStatus"],
        "syntheticTraffic.errorHttpStatus",
        minimum=400,
        maximum=599,
        integer=True,
    )

    profiles = _require_dict(root["profiles"], "profiles")
    slo_profiles = _require_dict(root["sloProfiles"], "sloProfiles")
    if profile_name not in profiles:
        raise ConfigurationError(f"unknown campaign profile: {profile_name}")
    if profile_name not in slo_profiles:
        raise ConfigurationError(f"missing SLO profile: {profile_name}")

    profile = dict(_require_dict(profiles[profile_name], f"profiles.{profile_name}"))
    profile_keys = {
        "repetitions",
        "soakSecondsTotal",
        "capacityConcurrencies",
        "capacitySecondsPerPoint",
        "soakConcurrency",
        "warmupRequestsPerWorker",
        "readinessRestartCycles",
        "replayRequestsPerArm",
        "replayRestartCycles",
        "maxTokens",
        "requestTimeoutSeconds",
        "readyTimeoutSeconds",
        "minMeasuredRequestsPerArmRun",
    }
    _require_keys(profile, f"profiles.{profile_name}", profile_keys)
    _reject_unknown_keys(profile, f"profiles.{profile_name}", profile_keys)

    integer_positive = {
        "repetitions",
        "soakConcurrency",
        "warmupRequestsPerWorker",
        "readinessRestartCycles",
        "replayRequestsPerArm",
        "replayRestartCycles",
        "maxTokens",
        "minMeasuredRequestsPerArmRun",
    }
    for key in integer_positive:
        minimum = 0 if key in {"readinessRestartCycles", "replayRestartCycles"} else 1
        _require_number(
            profile[key],
            f"profiles.{profile_name}.{key}",
            minimum=minimum,
            integer=True,
        )
    for key in {
        "soakSecondsTotal",
        "capacitySecondsPerPoint",
        "requestTimeoutSeconds",
        "readyTimeoutSeconds",
    }:
        _require_number(
            profile[key], f"profiles.{profile_name}.{key}", minimum=0.001
        )
    concurrencies = profile["capacityConcurrencies"]
    if not isinstance(concurrencies, list) or not concurrencies:
        raise ConfigurationError("capacityConcurrencies must be a non-empty list")
    for index, concurrency in enumerate(concurrencies):
        _require_number(
            concurrency,
            f"profiles.{profile_name}.capacityConcurrencies[{index}]",
            minimum=1,
            integer=True,
        )
    if len(concurrencies) != len(set(concurrencies)):
        raise ConfigurationError("capacityConcurrencies must not contain duplicates")

    slo = dict(_require_dict(slo_profiles[profile_name], f"sloProfiles.{profile_name}"))
    slo_keys = {
        "maxReadinessErrorRate",
        "maxTtftP99Ms",
        "maxE2eP99Ms",
        "maxRecoverySeconds",
        "maxRssMiB",
        "maxReplayUnexpectedErrorRate",
        "maxReplayE2eP99Ms",
        "maxForeignSentinelLeaks",
        "maxCandidateErrorRateDelta",
        "maxCandidateTtftRatio",
        "maxCandidateE2eRatio",
        "minCandidateThroughputRatio",
    }
    _require_keys(slo, f"sloProfiles.{profile_name}", slo_keys)
    _reject_unknown_keys(slo, f"sloProfiles.{profile_name}", slo_keys)
    for key in slo_keys:
        minimum = 0
        maximum = 1 if key.endswith("ErrorRate") or key.endswith("ErrorRateDelta") else None
        integer = key == "maxForeignSentinelLeaks"
        _require_number(
            slo[key],
            f"sloProfiles.{profile_name}.{key}",
            minimum=minimum,
            maximum=maximum,
            integer=integer,
        )
    if float(slo["maxCandidateTtftRatio"]) <= 0:
        raise ConfigurationError("maxCandidateTtftRatio must be > 0")
    if float(slo["maxCandidateE2eRatio"]) <= 0:
        raise ConfigurationError("maxCandidateE2eRatio must be > 0")
    if float(slo["minCandidateThroughputRatio"]) <= 0:
        raise ConfigurationError("minCandidateThroughputRatio must be > 0")

    if repetitions_override is not None:
        _require_number(
            repetitions_override, "repetitions override", minimum=1, integer=True
        )
        profile["repetitions"] = repetitions_override
    if soak_seconds_override is not None:
        _require_number(soak_seconds_override, "soak override", minimum=0.001)
        profile["soakSecondsTotal"] = soak_seconds_override

    slots = int(profile["repetitions"]) * 2
    if float(profile["soakSecondsTotal"]) / slots <= 0:
        raise ConfigurationError("resolved per-arm soak duration must be positive")
    if int(profile["replayRestartCycles"]) >= int(profile["replayRequestsPerArm"]):
        raise ConfigurationError(
            "replayRestartCycles must be smaller than replayRequestsPerArm"
        )

    return {
        "schema": CONFIG_SCHEMA,
        "source": str(path),
        "profileName": profile_name,
        "claims": dict(CLAIM_FLAGS),
        "syntheticTraffic": traffic,
        "profile": profile,
        "slo": slo,
        "configSha256": sha256_file(path),
    }


def sentinel_for(
    seed: int, repetition: int, tenant_label: str, campaign_shard: int = 1
) -> str:
    material = f"{seed}:{campaign_shard}:{repetition}:{tenant_label}".encode("ascii")
    return "PGSYNTH-" + hashlib.sha256(material).hexdigest()[:20].upper()


def prompt_contains_pii(prompt: str) -> bool:
    return bool(_EMAIL_RE.search(prompt) or _PHONE_RE.search(prompt))


def build_synthetic_trace(
    config: dict[str, Any], repetition: int, campaign_shard: int = 1
) -> list[TrafficItem]:
    """Create a deterministic, generated-only trace with no user-supplied text."""
    if campaign_shard <= 0:
        raise ConfigurationError("campaign shard must be positive")
    traffic = config["syntheticTraffic"]
    profile = config["profile"]
    labels = list(traffic["tenantLabels"])
    sentinels = {
        label: sentinel_for(
            int(traffic["seed"]), repetition, label, campaign_shard
        )
        for label in labels
    }
    count = int(profile["replayRequestsPerArm"])
    delay_every = int(traffic["delayEveryRequests"])
    error_every = int(traffic["errorEveryRequests"])
    rows: list[TrafficItem] = []

    for sequence in range(1, count + 1):
        label = labels[(sequence - 1) % len(labels)]
        sentinel = sentinels[label]
        prompt = (
            "Synthetic validation traffic only; no customer or personal data is "
            f"present. Active label: {label}. Active sentinel: {sentinel}. "
            "Return one short sentence containing the active sentinel. Do not "
            "invent or repeat any other sentinel."
        )
        contains_pii = prompt_contains_pii(prompt)
        row = TrafficItem(
            schema=TRACE_SCHEMA,
            campaignShard=campaign_shard,
            repetition=repetition,
            sequence=sequence,
            requestId=(
                f"synthetic-s{campaign_shard:03d}-"
                f"r{repetition:03d}-q{sequence:05d}"
            ),
            tenantLabel=label,
            sentinel=sentinel,
            prompt=prompt,
            promptSha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            containsPii=contains_pii,
            injectedDelayMs=(
                int(traffic["delayMilliseconds"])
                if delay_every > 0 and sequence % delay_every == 0
                else 0
            ),
            injectLoadGeneratorError=(
                error_every > 0 and sequence % error_every == 0
            ),
        )
        rows.append(row)

    validate_synthetic_trace(rows, labels)
    return rows


def validate_synthetic_trace(
    rows: Sequence[TrafficItem], tenant_labels: Sequence[str]
) -> None:
    if not rows:
        raise ConfigurationError("synthetic trace must not be empty")
    expected_sequences = list(range(1, len(rows) + 1))
    if [row.sequence for row in rows] != expected_sequences:
        raise ConfigurationError("synthetic trace sequence is not contiguous")
    seen_ids: set[str] = set()
    for row in rows:
        if row.schema != TRACE_SCHEMA:
            raise ConfigurationError("synthetic trace schema mismatch")
        if row.requestId in seen_ids:
            raise ConfigurationError("synthetic trace request ids must be unique")
        seen_ids.add(row.requestId)
        if row.tenantLabel not in tenant_labels or not _TENANT_RE.fullmatch(
            row.tenantLabel
        ):
            raise ConfigurationError("synthetic trace contains an invalid tenant label")
        if not _SENTINEL_RE.fullmatch(row.sentinel):
            raise ConfigurationError("synthetic trace contains an invalid sentinel")
        if row.containsPii or prompt_contains_pii(row.prompt):
            raise ConfigurationError("synthetic trace failed the no-PII assertion")
        if hashlib.sha256(row.prompt.encode("utf-8")).hexdigest() != row.promptSha256:
            raise ConfigurationError("synthetic trace prompt digest mismatch")
        if row.tenantLabel not in row.prompt or row.sentinel not in row.prompt:
            raise ConfigurationError("trace prompt is not scoped to its synthetic tenant")


def trace_digest(rows: Sequence[TrafficItem]) -> str:
    return canonical_sha256([dataclasses.asdict(row) for row in rows])


def _extract_response_text(obj: dict[str, Any]) -> str:
    text = obj.get("content")
    if isinstance(text, str):
        return text
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            candidate = choice.get("text")
            if isinstance(candidate, str):
                return candidate
    return ""


def send_replay_completion(
    host: str,
    port: int,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> CompletionObservation:
    """Send one synthetic replay request while retaining text only in memory."""
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
    status_code: int | None = None
    first_content_at: float | None = None
    output: list[str] = []
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
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
                text = _extract_response_text(obj)
                if text:
                    if first_content_at is None:
                        first_content_at = time.monotonic()
                    output.append(text)
                prompt_value, completion_value = readiness._extract_usage(obj)
                if prompt_value is not None:
                    prompt_tokens = prompt_value
                if completion_value is not None:
                    completion_tokens = completion_value
        else:
            payload = response.read()
            obj = json.loads(payload)
            text = _extract_response_text(obj)
            if text:
                first_content_at = time.monotonic()
                output.append(text)
            prompt_tokens, completion_tokens = readiness._extract_usage(obj)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()

    ended = time.monotonic()
    text = "".join(output)
    success = error is None and status_code == 200 and bool(text.strip())
    return CompletionObservation(
        status="success" if success else "error",
        httpStatus=status_code,
        text=text,
        ttftMs=(
            round((first_content_at - started) * 1000.0, 3)
            if first_content_at is not None
            else None
        ),
        e2eMs=round((ended - started) * 1000.0, 3),
        promptTokens=prompt_tokens,
        completionTokens=completion_tokens,
        error=error if error else (None if success else "empty-response"),
    )


def restart_positions(request_count: int, restart_cycles: int) -> set[int]:
    if restart_cycles <= 0:
        return set()
    positions: set[int] = set()
    for cycle in range(1, restart_cycles + 1):
        position = round(cycle * (request_count + 1) / (restart_cycles + 1))
        positions.add(min(request_count, max(2, position)))
    if len(positions) != restart_cycles:
        raise ConfigurationError("could not allocate distinct replay restart positions")
    return positions


def execute_trace(
    rows: Sequence[TrafficItem],
    *,
    sender: Callable[[TrafficItem], CompletionObservation],
    tenant_sentinels: Sequence[str],
    injected_error_http_status: int,
    sleep_fn: Callable[[float], None] = time.sleep,
    restart_at: set[int] | None = None,
    restart_hook: Callable[[int], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute a deterministic trace; injected errors never reach the server."""
    restart_at = restart_at or set()
    results: list[dict[str, Any]] = []
    restarts: list[dict[str, Any]] = []

    for row in rows:
        if row.sequence in restart_at:
            event: dict[str, Any] = {
                "faultType": "process-model-restart",
                "beforeSequence": row.sequence,
                "success": False,
                "readySeconds": None,
                "error": None,
            }
            try:
                if restart_hook is None:
                    raise RuntimeError("restart hook not configured")
                details = restart_hook(row.sequence)
                event.update(details)
                event["success"] = bool(details.get("success", True))
            except Exception as exc:
                event["error"] = f"{type(exc).__name__}: {exc}"
            restarts.append(event)

        started = time.monotonic()
        if row.injectedDelayMs:
            sleep_fn(row.injectedDelayMs / 1000.0)

        if row.injectLoadGeneratorError:
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
            results.append(
                {
                    "schema": REPLAY_RESULT_SCHEMA,
                    "requestId": row.requestId,
                    "sequence": row.sequence,
                    "tenantLabel": row.tenantLabel,
                    "sentinel": row.sentinel,
                    "status": "injected-error",
                    "expectedInjectedError": True,
                    "requestSentToServer": False,
                    "httpStatus": injected_error_http_status,
                    "injectedDelayMs": row.injectedDelayMs,
                    "loadGeneratorE2eMs": elapsed_ms,
                    "ttftMs": None,
                    "promptTokens": None,
                    "completionTokens": None,
                    "responseCharacters": 0,
                    "responseSha256": None,
                    "ownSentinelObserved": False,
                    "foreignSentinels": [],
                    "error": "synthetic-load-generator-error",
                }
            )
            continue

        try:
            observation = sender(row)
        except Exception as exc:
            observation = CompletionObservation(
                status="error",
                httpStatus=None,
                text="",
                ttftMs=None,
                e2eMs=0.0,
                promptTokens=None,
                completionTokens=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
        foreign = sorted(
            sentinel
            for sentinel in tenant_sentinels
            if sentinel != row.sentinel and sentinel in observation.text
        )
        results.append(
            {
                "schema": REPLAY_RESULT_SCHEMA,
                "requestId": row.requestId,
                "sequence": row.sequence,
                "tenantLabel": row.tenantLabel,
                "sentinel": row.sentinel,
                "status": (
                    "success" if observation.status == "success" else "unexpected-error"
                ),
                "expectedInjectedError": False,
                "requestSentToServer": True,
                "httpStatus": observation.httpStatus,
                "injectedDelayMs": row.injectedDelayMs,
                "loadGeneratorE2eMs": elapsed_ms,
                "serverE2eMs": observation.e2eMs,
                "ttftMs": observation.ttftMs,
                "promptTokens": observation.promptTokens,
                "completionTokens": observation.completionTokens,
                "responseCharacters": len(observation.text),
                "responseSha256": (
                    hashlib.sha256(observation.text.encode("utf-8")).hexdigest()
                    if observation.text
                    else None
                ),
                "ownSentinelObserved": row.sentinel in observation.text,
                "foreignSentinels": foreign,
                "error": observation.error,
            }
        )
    return results, restarts


def _check(name: str, passed: bool, observed: Any, limit: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "observed": observed,
        "limit": limit,
    }


def summarize_replay(
    results: Sequence[dict[str, Any]],
    restarts: Sequence[dict[str, Any]],
    *,
    expected_restart_cycles: int,
    slo: dict[str, Any],
) -> dict[str, Any]:
    expected_injected = sum(
        1 for row in results if row.get("expectedInjectedError") is True
    )
    observed_injected = sum(1 for row in results if row["status"] == "injected-error")
    sent = [row for row in results if row["requestSentToServer"]]
    successes = [row for row in sent if row["status"] == "success"]
    unexpected = [row for row in sent if row["status"] != "success"]
    own_sentinel_missing = [
        row for row in successes if row.get("ownSentinelObserved") is not True
    ]
    foreign_leaks = sum(len(row["foreignSentinels"]) for row in results)
    e2e_values = [float(row["loadGeneratorE2eMs"]) for row in successes]
    e2e_p99 = readiness.percentile(e2e_values, 99)
    unexpected_rate = len(unexpected) / len(sent) if sent else 1.0
    failed_restarts = [row for row in restarts if not row.get("success")]

    checks = [
        _check(
            "expected-load-generator-errors-observed",
            expected_injected == observed_injected,
            observed_injected,
            expected_injected,
        ),
        _check(
            "replay-unexpected-error-rate",
            unexpected_rate <= float(slo["maxReplayUnexpectedErrorRate"]),
            unexpected_rate,
            slo["maxReplayUnexpectedErrorRate"],
        ),
        _check(
            "replay-e2e-p99",
            e2e_p99 is not None
            and e2e_p99 <= float(slo["maxReplayE2eP99Ms"]),
            e2e_p99,
            slo["maxReplayE2eP99Ms"],
        ),
        _check(
            "synthetic-tenant-foreign-sentinel-leaks",
            foreign_leaks <= int(slo["maxForeignSentinelLeaks"]),
            foreign_leaks,
            slo["maxForeignSentinelLeaks"],
        ),
        _check(
            "synthetic-tenant-own-sentinel-present",
            not own_sentinel_missing,
            len(own_sentinel_missing),
            0,
        ),
        _check(
            "process-model-restart-count",
            len(restarts) == expected_restart_cycles,
            len(restarts),
            expected_restart_cycles,
        ),
        _check(
            "process-model-restarts-success",
            len(restarts) == expected_restart_cycles and not failed_restarts,
            len(failed_restarts),
            0,
        ),
    ]
    return {
        "verdict": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "requests": len(results),
        "requestsSentToServer": len(sent),
        "successes": len(successes),
        "expectedInjectedErrors": expected_injected,
        "observedInjectedErrors": observed_injected,
        "unexpectedErrors": len(unexpected),
        "unexpectedErrorRate": unexpected_rate,
        "foreignSentinelLeaks": foreign_leaks,
        "ownSentinelMissingSuccesses": len(own_sentinel_missing),
        "loadGeneratorE2eP99Ms": e2e_p99,
        "restartEvents": list(restarts),
        "checks": checks,
        "tenantIsolationClaimed": False,
        "boundary": (
            "Foreign-sentinel checks cover only this generated trace and do not "
            "establish real multi-tenant isolation."
        ),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            value = dataclasses.asdict(row) if dataclasses.is_dataclass(row) else row
            handle.write(json.dumps(value, sort_keys=True) + "\n")


def prepare_output_dir(requested: Path) -> Path:
    """Create one new private output root; existing state is never reused."""
    if requested.exists() or requested.is_symlink():
        raise ConfigurationError(
            f"output directory must be new and non-symlink: {requested}"
        )
    requested.parent.mkdir(parents=True, exist_ok=True)
    parent = requested.parent.resolve(strict=True)
    out_dir = parent / requested.name
    if out_dir.exists() or out_dir.is_symlink():
        raise ConfigurationError(
            f"resolved output directory already exists or is a symlink: {out_dir}"
        )
    out_dir.mkdir(mode=0o700)
    os.chmod(out_dir, 0o700)
    mode = stat.S_IMODE(out_dir.stat().st_mode)
    if mode != 0o700:
        raise ConfigurationError(
            f"output directory mode must be 0700, observed {mode:04o}"
        )
    return out_dir.resolve(strict=True)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_tree_files(out_dir: Path) -> tuple[list[Path], list[str]]:
    errors: list[str] = []
    if out_dir.is_symlink():
        return [], ["output root must not be a symlink"]
    try:
        root = out_dir.resolve(strict=True)
    except OSError as exc:
        return [], [f"cannot resolve output root: {exc}"]
    if not root.is_dir():
        return [], ["output root is not a directory"]

    files: list[Path] = []
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        try:
            current_resolved = current_path.resolve(strict=True)
        except OSError as exc:
            errors.append(f"cannot resolve directory {current_path}: {exc}")
            dir_names[:] = []
            continue
        if not _is_within(current_resolved, root):
            errors.append(f"directory resolves outside output root: {current_path}")
            dir_names[:] = []
            continue
        for name in list(dir_names):
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                errors.append(f"symlink directory is forbidden: {relative}")
                dir_names.remove(name)
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                errors.append(f"cannot resolve directory {relative}: {exc}")
                dir_names.remove(name)
                continue
            if not _is_within(resolved, root):
                errors.append(f"directory resolves outside output root: {relative}")
                dir_names.remove(name)
        for name in file_names:
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                errors.append(f"symlink file is forbidden: {relative}")
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                errors.append(f"cannot resolve file {relative}: {exc}")
                continue
            if not _is_within(resolved, root):
                errors.append(f"file resolves outside output root: {relative}")
                continue
            if not path.is_file():
                errors.append(f"non-regular output entry: {relative}")
                continue
            files.append(path)
    return sorted(files), errors


def write_checksums(out_dir: Path) -> None:
    if out_dir.is_symlink():
        raise ConfigurationError("output root must not be a symlink")
    root = out_dir.resolve(strict=True)
    manifest = root / "sha256sums.txt"
    if manifest.is_symlink():
        raise ConfigurationError("checksum manifest must not be a symlink")
    files, errors = _safe_tree_files(root)
    if errors:
        raise ConfigurationError("; ".join(errors))
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in files
        if path != manifest
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_checksums(out_dir: Path) -> dict[str, Any]:
    if out_dir.is_symlink():
        return {
            "valid": False,
            "errors": ["output root must not be a symlink"],
            "files": 0,
        }
    try:
        root = out_dir.resolve(strict=True)
    except OSError as exc:
        return {
            "valid": False,
            "errors": [f"cannot resolve output root: {exc}"],
            "files": 0,
        }
    manifest = root / "sha256sums.txt"
    if manifest.is_symlink():
        return {
            "valid": False,
            "errors": ["sha256sums.txt must not be a symlink"],
            "files": 0,
        }
    if not manifest.is_file():
        return {"valid": False, "errors": ["sha256sums.txt is missing"], "files": 0}

    expected: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            errors.append(f"malformed checksum line {line_number}")
            continue
        relative = Path(parts[1])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe checksum path on line {line_number}")
            continue
        key = relative.as_posix()
        if key in expected:
            errors.append(f"duplicate checksum path: {key}")
            continue
        expected[key] = parts[0]

    safe_files, tree_errors = _safe_tree_files(root)
    errors.extend(tree_errors)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in safe_files
        if path != manifest
    }
    for missing in sorted(actual_paths - set(expected)):
        errors.append(f"unlisted file: {missing}")
    for extra in sorted(set(expected) - actual_paths):
        errors.append(f"listed file missing: {extra}")
    for relative, digest in sorted(expected.items()):
        path = root / relative
        if path.is_symlink():
            errors.append(f"symlink checksum target: {relative}")
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not _is_within(resolved, root):
            errors.append(f"checksum target resolves outside output root: {relative}")
            continue
        if path.is_file() and sha256_file(path) != digest:
            errors.append(f"checksum mismatch: {relative}")
    return {"valid": not errors, "errors": errors, "files": len(expected)}


def _readiness_args(
    *,
    server: Path,
    model: Path,
    out_dir: Path,
    host: str,
    port: int,
    profile: dict[str, Any],
    slo: dict[str, Any],
    soak_seconds: float,
) -> list[str]:
    return [
        "--server",
        str(server),
        "--model",
        str(model),
        "--out-dir",
        str(out_dir),
        "--host",
        host,
        "--port",
        str(port),
        "--threads",
        str(max(1, os.cpu_count() or 1)),
        "--threads-batch",
        str(max(1, os.cpu_count() or 1)),
        "--server-parallel",
        str(
            max(
                int(profile["soakConcurrency"]),
                max(int(value) for value in profile["capacityConcurrencies"]),
            )
        ),
        "--capacity-concurrencies",
        ",".join(str(value) for value in profile["capacityConcurrencies"]),
        "--capacity-seconds",
        str(profile["capacitySecondsPerPoint"]),
        "--soak-concurrency",
        str(profile["soakConcurrency"]),
        "--soak-seconds",
        str(soak_seconds),
        "--warmup-requests-per-worker",
        str(profile["warmupRequestsPerWorker"]),
        "--restart-cycles",
        str(profile["readinessRestartCycles"]),
        "--max-tokens",
        str(profile["maxTokens"]),
        "--request-timeout",
        str(profile["requestTimeoutSeconds"]),
        "--ready-timeout",
        str(profile["readyTimeoutSeconds"]),
        "--min-measured-requests",
        str(profile["minMeasuredRequestsPerArmRun"]),
        "--max-error-rate",
        str(slo["maxReadinessErrorRate"]),
        "--max-ttft-p99-ms",
        str(slo["maxTtftP99Ms"]),
        "--max-e2e-p99-ms",
        str(slo["maxE2eP99Ms"]),
        "--max-recovery-seconds",
        str(slo["maxRecoverySeconds"]),
        "--max-rss-mib",
        str(slo["maxRssMiB"]),
    ]


def run_readiness_measurement(
    *,
    server: Path,
    model: Path,
    out_dir: Path,
    host: str,
    port: int,
    profile: dict[str, Any],
    slo: dict[str, Any],
    soak_seconds: float,
    artifact_guard: ArtifactDriftGuard,
) -> dict[str, Any]:
    readiness_out = out_dir / "readiness"
    args = _readiness_args(
        server=server,
        model=model,
        out_dir=readiness_out,
        host=host,
        port=port,
        profile=profile,
        slo=slo,
        soak_seconds=soak_seconds,
    )
    stdout_path = out_dir / "server-readiness.stdout.json"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    exit_code: int | None = None
    error: str | None = None
    original_controller = readiness.ServerController

    class GuardedServerController(original_controller):
        def start(self, label: str) -> float:
            artifact_guard.snapshot(f"readiness:{label}:before-start")
            ready_seconds = super().start(label)
            artifact_guard.snapshot(f"readiness:{label}:after-start")
            return ready_seconds

        def stop(self) -> dict[str, Any]:
            artifact_guard.snapshot("readiness:before-stop", fail=False)
            result = super().stop()
            artifact_guard.snapshot("readiness:after-stop", fail=False)
            artifact_guard.raise_if_drift()
            return result

    try:
        artifact_guard.snapshot("readiness-arm:before")
        readiness.ServerController = GuardedServerController
        with stdout_path.open("w", encoding="utf-8") as stdout_handle:
            with contextlib.redirect_stdout(stdout_handle):
                exit_code = int(readiness.main(args))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        readiness.ServerController = original_controller
        artifact_guard.snapshot("readiness-arm:after", fail=False)
        if artifact_guard.errors and error is None:
            error = "ArtifactDriftError: " + "; ".join(artifact_guard.errors)

    summary_path = readiness_out / "summary.json"
    summary: dict[str, Any] | None = None
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            error = f"invalid readiness summary: {exc}"
    if summary is not None:
        for key, expected in CLAIM_FLAGS.items():
            if summary.get(key) is not expected:
                error = f"readiness summary boundary mismatch: {key}"
        if summary.get("schema") != readiness.SCHEMA:
            error = "readiness summary schema mismatch"
    elif error is None:
        error = "readiness summary missing"

    receipt = {
        "exitCode": exit_code,
        "error": error,
        "configuration": {
            "host": host,
            "port": port,
            "soakSeconds": soak_seconds,
            "soakConcurrency": profile["soakConcurrency"],
            "restartCycles": profile["readinessRestartCycles"],
        },
        "summaryPath": "readiness/summary.json",
        "summary": summary,
        **CLAIM_FLAGS,
    }
    write_json(out_dir / "readiness-invocation.json", receipt)
    return receipt


def run_replay_measurement(
    *,
    server: Path,
    model: Path,
    out_dir: Path,
    host: str,
    port: int,
    profile: dict[str, Any],
    slo: dict[str, Any],
    traffic: dict[str, Any],
    trace: Sequence[TrafficItem],
    artifact_guard: ArtifactDriftGuard,
) -> dict[str, Any]:
    replay_dir = out_dir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    controller = readiness.ServerController(
        server=server,
        model=model,
        host=host,
        port=port,
        threads=max(1, os.cpu_count() or 1),
        threads_batch=max(1, os.cpu_count() or 1),
        parallel=max(1, int(profile["soakConcurrency"])),
        context_size=2048,
        ready_timeout=float(profile["readyTimeoutSeconds"]),
        log_path=replay_dir / "llama-server.log",
    )
    results: list[dict[str, Any]] = []
    restart_events: list[dict[str, Any]] = []
    infrastructure_error: str | None = None
    cold_ready_seconds: float | None = None
    final_stop: dict[str, Any] | None = None
    tenant_sentinels = sorted({row.sentinel for row in trace})

    def sender(row: TrafficItem) -> CompletionObservation:
        return send_replay_completion(
            host,
            port,
            row.prompt,
            int(profile["maxTokens"]),
            float(profile["requestTimeoutSeconds"]),
        )

    def restart_hook(sequence: int) -> dict[str, Any]:
        artifact_guard.snapshot(
            f"replay:restart-q{sequence}:before-stop", fail=False
        )
        stop = controller.stop()
        artifact_guard.snapshot(
            f"replay:restart-q{sequence}:after-stop", fail=False
        )
        artifact_guard.raise_if_drift()
        artifact_guard.snapshot(f"replay:restart-q{sequence}:before-start")
        ready_seconds = controller.start(f"replay-process-model-restart-q{sequence}")
        artifact_guard.snapshot(f"replay:restart-q{sequence}:after-start")
        return {
            "success": True,
            "stop": stop,
            "readySeconds": ready_seconds,
            "modelReloaded": True,
        }

    try:
        artifact_guard.snapshot("replay-arm:before")
        artifact_guard.snapshot("replay:cold-start:before")
        cold_ready_seconds = controller.start("replay-cold-start")
        artifact_guard.snapshot("replay:cold-start:after")
        results, restart_events = execute_trace(
            trace,
            sender=sender,
            tenant_sentinels=tenant_sentinels,
            injected_error_http_status=int(traffic["errorHttpStatus"]),
            restart_at=restart_positions(
                len(trace), int(profile["replayRestartCycles"])
            ),
            restart_hook=restart_hook,
        )
    except Exception as exc:
        infrastructure_error = f"{type(exc).__name__}: {exc}"
    finally:
        artifact_guard.snapshot("replay:final-stop:before", fail=False)
        try:
            try:
                final_stop = controller.stop()
            except Exception as exc:
                if infrastructure_error is None:
                    infrastructure_error = f"{type(exc).__name__}: {exc}"
        finally:
            artifact_guard.snapshot("replay:final-stop:after", fail=False)
            artifact_guard.snapshot("replay-arm:after", fail=False)
        if artifact_guard.errors and infrastructure_error is None:
            infrastructure_error = (
                "ArtifactDriftError: " + "; ".join(artifact_guard.errors)
            )

    summary = summarize_replay(
        results,
        restart_events,
        expected_restart_cycles=int(profile["replayRestartCycles"]),
        slo=slo,
    )
    if infrastructure_error:
        summary["verdict"] = "UNDETERMINED"
        summary["infrastructureError"] = infrastructure_error
    summary.update(
        {
            "coldReadySeconds": cold_ready_seconds,
            "finalStop": final_stop,
            "traceDigest": trace_digest(trace),
            "syntheticTrafficOnly": True,
            "containsPii": False,
            **CLAIM_FLAGS,
        }
    )
    write_jsonl(replay_dir / "requests.jsonl", results)
    write_json(replay_dir / "summary.json", summary)
    return summary


def _artifact_identity(path: Path) -> dict[str, Any]:
    is_symlink = path.is_symlink()
    is_file = path.is_file()
    if not is_file or is_symlink:
        return {
            "path": str(path),
            "resolvedPath": None,
            "exists": False,
            "isFile": is_file,
            "isSymlink": is_symlink,
            "sizeBytes": None,
            "sha256": None,
        }
    resolved = path.resolve(strict=True)
    return {
        "path": str(path),
        "resolvedPath": str(resolved),
        "exists": True,
        "isFile": True,
        "isSymlink": False,
        "sizeBytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


class ArtifactDriftGuard:
    """Re-hash immutable server/model files around every arm and restart."""

    def __init__(
        self,
        *,
        server: Path,
        model: Path,
        expected_server: dict[str, Any],
        expected_model: dict[str, Any],
    ) -> None:
        self.server = server
        self.model = model
        self.expected = {
            "server": {
                "sha256": expected_server.get("sha256"),
                "sizeBytes": expected_server.get("sizeBytes"),
            },
            "model": {
                "sha256": expected_model.get("sha256"),
                "sizeBytes": expected_model.get("sizeBytes"),
            },
        }
        self.snapshots: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def snapshot(self, label: str, *, fail: bool = True) -> dict[str, Any]:
        observed = {
            "server": _artifact_identity(self.server),
            "model": _artifact_identity(self.model),
        }
        snapshot_errors: list[str] = []
        for kind in ("server", "model"):
            identity = observed[kind]
            if identity.get("isSymlink"):
                snapshot_errors.append(f"{label}: {kind} became a symlink")
            if not identity.get("exists"):
                snapshot_errors.append(f"{label}: {kind} is missing or not regular")
                continue
            if identity.get("sha256") != self.expected[kind]["sha256"]:
                snapshot_errors.append(f"{label}: {kind} sha256 drift")
            if identity.get("sizeBytes") != self.expected[kind]["sizeBytes"]:
                snapshot_errors.append(f"{label}: {kind} size drift")
        record = {
            "label": label,
            "timestampUtc": utc_now(),
            "serverSha256": observed["server"].get("sha256"),
            "serverSizeBytes": observed["server"].get("sizeBytes"),
            "modelSha256": observed["model"].get("sha256"),
            "modelSizeBytes": observed["model"].get("sizeBytes"),
            "passed": not snapshot_errors,
            "errors": snapshot_errors,
        }
        self.snapshots.append(record)
        for error in snapshot_errors:
            if error not in self.errors:
                self.errors.append(error)
        if fail and snapshot_errors:
            raise RuntimeError("; ".join(snapshot_errors))
        return record

    def raise_if_drift(self) -> None:
        if self.errors:
            raise RuntimeError("; ".join(self.errors))

    def receipt(self) -> dict[str, Any]:
        return {
            "verdict": "PASS" if not self.errors else "UNDETERMINED",
            "expected": self.expected,
            "snapshots": list(self.snapshots),
            "errors": list(self.errors),
        }


def aggregate_arm(
    label: str, runs: Sequence[dict[str, Any]], repetitions: int
) -> dict[str, Any]:
    readiness_summaries = [
        run["readiness"]["summary"]
        for run in runs
        if isinstance(run["readiness"].get("summary"), dict)
    ]
    replay_summaries = [
        run["replay"] for run in runs if isinstance(run.get("replay"), dict)
    ]
    soak_rows = [
        row
        for summary in readiness_summaries
        for row in summary.get("results", [])
        if row.get("phase") == "soak"
    ]
    total_requests = sum(
        int(summary.get("totals", {}).get("measuredRequests", 0))
        for summary in readiness_summaries
    )
    total_failures = sum(
        int(summary.get("totals", {}).get("measuredFailures", 0))
        for summary in readiness_summaries
    )
    throughput = [
        float(row["outputTokensPerSecond"])
        for row in soak_rows
        if row.get("outputTokensPerSecond") is not None
    ]
    ttft = [
        float(row["ttftP99Ms"])
        for row in soak_rows
        if row.get("ttftP99Ms") is not None
    ]
    e2e = [
        float(row["e2eP99Ms"])
        for row in soak_rows
        if row.get("e2eP99Ms") is not None
    ]
    recovery_values = [
        float(restart["readySeconds"])
        for summary in readiness_summaries
        for restart in summary.get("restarts", [])
        if restart.get("readySeconds") is not None
    ] + [
        float(restart["readySeconds"])
        for summary in replay_summaries
        for restart in summary.get("restartEvents", [])
        if restart.get("readySeconds") is not None
    ]
    infrastructure_errors = [
        error
        for run in runs
        for error in (
            run["readiness"].get("error"),
            run.get("replay", {}).get("infrastructureError"),
        )
        if error
    ]
    infrastructure_errors.extend(
        error
        for run in runs
        for error in run.get("artifactIntegrity", {}).get("errors", [])
        if error
    )
    return {
        "label": label,
        "completedRuns": len(runs),
        "expectedRuns": repetitions,
        "readinessSummaries": len(readiness_summaries),
        "readinessGatePasses": sum(
            1
            for summary in readiness_summaries
            if summary.get("gate", {}).get("verdict") == "PASS"
        ),
        "replayGatePasses": sum(
            1 for summary in replay_summaries if summary.get("verdict") == "PASS"
        ),
        "measuredRequests": total_requests,
        "measuredFailures": total_failures,
        "measuredErrorRate": (
            total_failures / total_requests if total_requests else None
        ),
        "soakTtftP99WorstMs": max(ttft) if ttft else None,
        "soakE2eP99WorstMs": max(e2e) if e2e else None,
        "soakOutputTokensPerSecondMedian": (
            readiness.percentile(throughput, 50) if throughput else None
        ),
        "maxRestartRecoverySeconds": (
            max(recovery_values) if recovery_values else None
        ),
        "foreignSentinelLeaks": sum(
            int(summary.get("foreignSentinelLeaks", 0))
            for summary in replay_summaries
        ),
        "ownSentinelMissingSuccesses": sum(
            int(summary.get("ownSentinelMissingSuccesses", 0))
            for summary in replay_summaries
        ),
        "unexpectedReplayErrors": sum(
            int(summary.get("unexpectedErrors", 0)) for summary in replay_summaries
        ),
        "expectedInjectedErrors": sum(
            int(summary.get("expectedInjectedErrors", 0))
            for summary in replay_summaries
        ),
        "observedInjectedErrors": sum(
            int(summary.get("observedInjectedErrors", 0))
            for summary in replay_summaries
        ),
        "traceDigests": [run["traceDigest"] for run in runs],
        "configuredSoakSeconds": sum(float(run["soakSeconds"]) for run in runs),
        "infrastructureErrors": infrastructure_errors,
        "tenantIsolationClaimed": False,
        **CLAIM_FLAGS,
    }


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None:
        return None
    denominator_f = float(denominator)
    if denominator_f <= 0:
        return None
    return float(numerator) / denominator_f


def compare_candidate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    repetitions: int,
    configured_total_soak_seconds: float,
    slo: dict[str, Any],
    same_artifact_control: bool,
) -> dict[str, Any]:
    error_delta = (
        float(candidate["measuredErrorRate"]) - float(baseline["measuredErrorRate"])
        if candidate["measuredErrorRate"] is not None
        and baseline["measuredErrorRate"] is not None
        else None
    )
    ttft_ratio = _ratio(
        candidate["soakTtftP99WorstMs"], baseline["soakTtftP99WorstMs"]
    )
    e2e_ratio = _ratio(
        candidate["soakE2eP99WorstMs"], baseline["soakE2eP99WorstMs"]
    )
    throughput_ratio = _ratio(
        candidate["soakOutputTokensPerSecondMedian"],
        baseline["soakOutputTokensPerSecondMedian"],
    )
    paired_traces = (
        len(baseline["traceDigests"]) == repetitions
        and len(candidate["traceDigests"]) == repetitions
        and baseline["traceDigests"] == candidate["traceDigests"]
    )
    total_soak = (
        float(baseline["configuredSoakSeconds"])
        + float(candidate["configuredSoakSeconds"])
    )
    checks = [
        _check(
            "baseline-repetitions-complete",
            baseline["completedRuns"] == repetitions
            and baseline["readinessSummaries"] == repetitions,
            baseline["completedRuns"],
            repetitions,
        ),
        _check(
            "candidate-repetitions-complete",
            candidate["completedRuns"] == repetitions
            and candidate["readinessSummaries"] == repetitions,
            candidate["completedRuns"],
            repetitions,
        ),
        _check(
            "baseline-readiness-gates",
            baseline["readinessGatePasses"] == repetitions,
            baseline["readinessGatePasses"],
            repetitions,
        ),
        _check(
            "candidate-readiness-gates",
            candidate["readinessGatePasses"] == repetitions,
            candidate["readinessGatePasses"],
            repetitions,
        ),
        _check(
            "baseline-replay-gates",
            baseline["replayGatePasses"] == repetitions,
            baseline["replayGatePasses"],
            repetitions,
        ),
        _check(
            "candidate-replay-gates",
            candidate["replayGatePasses"] == repetitions,
            candidate["replayGatePasses"],
            repetitions,
        ),
        _check("paired-synthetic-traces", paired_traces, paired_traces, True),
        _check(
            "configured-total-soak-seconds",
            math.isclose(
                total_soak,
                float(configured_total_soak_seconds),
                rel_tol=0,
                abs_tol=1e-6,
            ),
            total_soak,
            configured_total_soak_seconds,
        ),
        _check(
            "candidate-error-rate-delta",
            error_delta is not None
            and error_delta <= float(slo["maxCandidateErrorRateDelta"]),
            error_delta,
            slo["maxCandidateErrorRateDelta"],
        ),
        _check(
            "candidate-ttft-ratio",
            ttft_ratio is not None
            and ttft_ratio <= float(slo["maxCandidateTtftRatio"]),
            ttft_ratio,
            slo["maxCandidateTtftRatio"],
        ),
        _check(
            "candidate-e2e-ratio",
            e2e_ratio is not None
            and e2e_ratio <= float(slo["maxCandidateE2eRatio"]),
            e2e_ratio,
            slo["maxCandidateE2eRatio"],
        ),
        _check(
            "candidate-throughput-ratio",
            throughput_ratio is not None
            and throughput_ratio >= float(slo["minCandidateThroughputRatio"]),
            throughput_ratio,
            slo["minCandidateThroughputRatio"],
        ),
    ]
    infrastructure_errors = (
        list(baseline["infrastructureErrors"])
        + list(candidate["infrastructureErrors"])
    )
    baseline_valid = (
        baseline["completedRuns"] == repetitions
        and baseline["readinessGatePasses"] == repetitions
        and baseline["replayGatePasses"] == repetitions
        and not baseline["infrastructureErrors"]
    )
    all_passed = all(check["passed"] for check in checks)
    if infrastructure_errors or not baseline_valid:
        verdict = "HOLD_UNDETERMINED"
        gate_verdict = "UNDETERMINED"
    elif all_passed:
        verdict = "KEEP_CANDIDATE"
        gate_verdict = "PASS"
    else:
        verdict = "ROLLBACK_TO_BASELINE"
        gate_verdict = "FAIL"
    return {
        "rollbackVerdict": verdict,
        "gateVerdict": gate_verdict,
        "checks": checks,
        "metrics": {
            "candidateErrorRateDelta": error_delta,
            "candidateTtftRatio": ttft_ratio,
            "candidateE2eRatio": e2e_ratio,
            "candidateThroughputRatio": throughput_ratio,
        },
        "sameArtifactControl": same_artifact_control,
        "infrastructureErrors": infrastructure_errors,
        "deploymentAuthorized": False,
        "boundary": (
            "KEEP_CANDIDATE means only that this synthetic candidate gate did "
            "not regress against its paired baseline. It is not deployment "
            "authorization or evidence of production readiness."
        ),
        **CLAIM_FLAGS,
    }


def run_campaign(args: argparse.Namespace) -> int:
    started_at = utc_now()
    try:
        out_dir = prepare_output_dir(args.out_dir.absolute())
    except ConfigurationError as exc:
        print(f"ConfigurationError: {exc}", file=sys.stderr)
        return 2

    try:
        if args.shard_id <= 0:
            raise ConfigurationError("shard id must be positive")
        reviewed_llama_sha = validate_reviewed_llama_sha(args.llama_cpp_sha)
        resolved_llama_sha = validate_reviewed_llama_sha(
            args.resolved_llama_cpp_sha
        )
        if reviewed_llama_sha != resolved_llama_sha:
            raise ConfigurationError("resolved llama.cpp SHA does not match allowlist")
        if args.hosted_workflow:
            if args.source_ref != "refs/heads/main":
                raise ConfigurationError(
                    "hosted production evidence is restricted to refs/heads/main"
                )
            if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
                raise ConfigurationError(
                    "hosted source SHA must be an exact lowercase 40-hex commit"
                )
            if not args.source_repository or args.source_repository == "local":
                raise ConfigurationError("hosted source repository is missing")
        config = load_config(
            args.config,
            args.profile,
            repetitions_override=args.repetitions,
            soak_seconds_override=args.soak_seconds_total,
        )
        model_manifest_entry = load_model_manifest_entry(
            args.model_manifest.absolute(), args.model_id
        )
        config["modelIdentity"] = model_manifest_entry
        provenance = {
            "llamaCpp": {
                "allowlistedShas": [REVIEWED_LLAMA_CPP_SHA],
                "reviewedSha": reviewed_llama_sha,
                "resolvedSha": resolved_llama_sha,
                "mutableRefAccepted": False,
            },
            "campaignSource": {
                "hostedWorkflow": bool(args.hosted_workflow),
                "repository": args.source_repository,
                "ref": args.source_ref,
                "sha": args.source_sha,
            },
        }
    except ConfigurationError as exc:
        receipt = {
            "schema": SCHEMA,
            "startedAtUtc": started_at,
            "completedAtUtc": utc_now(),
            "outputDirectoryMode": "0700",
            "gate": {
                "verdict": "UNDETERMINED",
                "rollbackVerdict": "HOLD_UNDETERMINED",
                "infrastructureError": f"ConfigurationError: {exc}",
            },
            **CLAIM_FLAGS,
        }
        write_json(out_dir / "receipt.json", receipt)
        write_checksums(out_dir)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2

    baseline_server = args.baseline_server.absolute()
    baseline_model = args.baseline_model.absolute()
    candidate_server = (args.candidate_server or args.baseline_server).absolute()
    candidate_model = (args.candidate_model or args.baseline_model).absolute()
    artifacts = {
        "baseline": {
            "server": _artifact_identity(baseline_server),
            "model": _artifact_identity(baseline_model),
        },
        "candidate": {
            "server": _artifact_identity(candidate_server),
            "model": _artifact_identity(candidate_model),
        },
    }
    missing = [
        f"{arm}.{kind}"
        for arm, values in artifacts.items()
        for kind, identity in values.items()
        if not identity["exists"]
    ]
    model_identity = {
        **model_manifest_entry,
        "baselinePath": str(baseline_model),
        "candidatePath": str(candidate_model),
        "baselineActualSha256": artifacts["baseline"]["model"]["sha256"],
        "candidateActualSha256": artifacts["candidate"]["model"]["sha256"],
        "baselineFileNameMatchesManifest": baseline_model.name
        == model_manifest_entry["hfFile"],
        "candidateFileNameMatchesManifest": candidate_model.name
        == model_manifest_entry["hfFile"],
        "validated": False,
    }
    model_identity_errors: list[str] = []
    if artifacts["baseline"]["model"]["exists"]:
        if not model_identity["baselineFileNameMatchesManifest"]:
            model_identity_errors.append(
                "baseline model filename does not match manifest hfFile"
            )
        if (
            artifacts["baseline"]["model"]["sha256"]
            != model_manifest_entry["sha256"]
        ):
            model_identity_errors.append(
                "baseline model sha256 does not match manifest"
            )
    if artifacts["candidate"]["model"]["exists"]:
        if not model_identity["candidateFileNameMatchesManifest"]:
            model_identity_errors.append(
                "candidate model filename does not match manifest hfFile"
            )
        if (
            artifacts["candidate"]["model"]["sha256"]
            != model_manifest_entry["sha256"]
        ):
            model_identity_errors.append(
                "candidate model sha256 does not match manifest"
            )
    model_identity["validated"] = not missing and not model_identity_errors
    repetitions = int(config["profile"]["repetitions"])
    total_soak_seconds = float(config["profile"]["soakSecondsTotal"])
    soak_per_arm_run = total_soak_seconds / (2 * repetitions)
    same_artifact_control = (
        artifacts["baseline"]["server"]["sha256"]
        == artifacts["candidate"]["server"]["sha256"]
        and artifacts["baseline"]["model"]["sha256"]
        == artifacts["candidate"]["model"]["sha256"]
        and not missing
    )
    write_json(out_dir / "resolved-config.json", config)

    if missing or model_identity_errors:
        infrastructure_errors: list[str] = []
        if missing:
            infrastructure_errors.append("missing artifacts: " + ", ".join(missing))
        infrastructure_errors.extend(model_identity_errors)
        receipt = {
            "schema": SCHEMA,
            "startedAtUtc": started_at,
            "completedAtUtc": utc_now(),
            "profile": args.profile,
            "artifacts": artifacts,
            "modelIdentity": model_identity,
            "provenance": provenance,
            "outputDirectoryMode": "0700",
            "gate": {
                "verdict": "UNDETERMINED",
                "rollbackVerdict": "HOLD_UNDETERMINED",
                "infrastructureError": "; ".join(infrastructure_errors),
            },
            **CLAIM_FLAGS,
        }
        write_json(out_dir / "receipt.json", receipt)
        write_checksums(out_dir)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2

    arm_runs: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    for repetition in range(1, repetitions + 1):
        trace = build_synthetic_trace(config, repetition, args.shard_id)
        digest = trace_digest(trace)
        round_dir = out_dir / f"round-{repetition:03d}"
        write_jsonl(round_dir / "sanitized-traffic.jsonl", trace)
        write_json(
            round_dir / "traffic-receipt.json",
            {
                "schema": TRACE_SCHEMA,
                "campaignShard": args.shard_id,
                "repetition": repetition,
                "requests": len(trace),
                "traceDigest": digest,
                "syntheticTenantLabels": config["syntheticTraffic"]["tenantLabels"],
                "containsPii": False,
                "actualProductionTraffic": False,
                "tenantIsolationClaimed": False,
            },
        )

        for arm_name, server, model in (
            ("baseline", baseline_server, baseline_model),
            ("candidate", candidate_server, candidate_model),
        ):
            arm_dir = round_dir / arm_name
            artifact_guard = ArtifactDriftGuard(
                server=server,
                model=model,
                expected_server=artifacts[arm_name]["server"],
                expected_model=artifacts[arm_name]["model"],
            )
            artifact_guard.snapshot(f"{arm_name}-arm:before")
            readiness_receipt = run_readiness_measurement(
                server=server,
                model=model,
                out_dir=arm_dir,
                host=args.host,
                port=args.port,
                profile=config["profile"],
                slo=config["slo"],
                soak_seconds=soak_per_arm_run,
                artifact_guard=artifact_guard,
            )
            replay_receipt = run_replay_measurement(
                server=server,
                model=model,
                out_dir=arm_dir,
                host=args.host,
                port=args.port,
                profile=config["profile"],
                slo=config["slo"],
                traffic=config["syntheticTraffic"],
                trace=trace,
                artifact_guard=artifact_guard,
            )
            artifact_guard.snapshot(f"{arm_name}-arm:after", fail=False)
            run_receipt = {
                "arm": arm_name,
                "label": (
                    args.baseline_label
                    if arm_name == "baseline"
                    else args.candidate_label
                ),
                "repetition": repetition,
                "soakSeconds": soak_per_arm_run,
                "traceDigest": digest,
                "modelIdentity": model_identity,
                "provenance": provenance,
                "artifactIntegrity": artifact_guard.receipt(),
                "readiness": readiness_receipt,
                "replay": replay_receipt,
                **CLAIM_FLAGS,
            }
            write_json(arm_dir / "arm-receipt.json", run_receipt)
            arm_runs[arm_name].append(run_receipt)

    baseline = aggregate_arm(args.baseline_label, arm_runs["baseline"], repetitions)
    candidate = aggregate_arm(args.candidate_label, arm_runs["candidate"], repetitions)
    comparison = compare_candidate(
        baseline,
        candidate,
        repetitions=repetitions,
        configured_total_soak_seconds=total_soak_seconds,
        slo=config["slo"],
        same_artifact_control=same_artifact_control,
    )
    receipt = {
        "schema": SCHEMA,
        "startedAtUtc": started_at,
        "completedAtUtc": utc_now(),
        "profile": args.profile,
        "campaign": {
            "shardId": args.shard_id,
            "repetitions": repetitions,
            "configuredTotalSoakSeconds": total_soak_seconds,
            "soakSecondsPerArmRun": soak_per_arm_run,
            "longestContinuousSoakSegmentSeconds": soak_per_arm_run,
            "aggregateMeasuredSoakSeconds": total_soak_seconds,
            "trafficSource": "deterministic-generated-synthetic-only",
            "faultInjection": [
                "controlled-process-restart",
                "controlled-model-reload",
                "load-generator-delay",
                "load-generator-error",
            ],
        },
        "config": config,
        "artifacts": artifacts,
        "modelIdentity": model_identity,
        "provenance": provenance,
        "outputDirectoryMode": "0700",
        "baseline": baseline,
        "candidate": candidate,
        "gate": comparison,
        "syntheticTenantLeakageAssertion": {
            "baselineForeignSentinelLeaks": baseline["foreignSentinelLeaks"],
            "candidateForeignSentinelLeaks": candidate["foreignSentinelLeaks"],
            "tenantIsolationClaimed": False,
        },
        "receiptChecksumManifest": "sha256sums.txt",
        "boundary": (
            "Synthetic single-host Arm64 candidate evidence only. No actual "
            "production traffic or PII is replayed. Per-label sentinel checks "
            "do not establish real multi-tenant isolation. A passing comparison "
            "does not authorize deployment or establish production readiness."
        ),
        **CLAIM_FLAGS,
    }
    write_json(out_dir / "receipt.json", receipt)
    write_checksums(out_dir)
    verification = verify_checksums(out_dir)
    if not verification["valid"]:
        receipt["gate"] = {
            **comparison,
            "gateVerdict": "UNDETERMINED",
            "rollbackVerdict": "HOLD_UNDETERMINED",
            "checksumVerification": verification,
        }
        write_json(out_dir / "receipt.json", receipt)
        write_checksums(out_dir)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2

    print(json.dumps(receipt, indent=2, sort_keys=True))
    if comparison["gateVerdict"] == "PASS":
        return 0
    if comparison["gateVerdict"] == "FAIL":
        return 1
    return 2


def validate_full_receipt(
    receipt: Any,
    *,
    require_hosted_workflow: bool = False,
    expected_source_repository: str | None = None,
    expected_source_ref: str | None = None,
    expected_source_sha: str | None = None,
) -> list[str]:
    """Strict structural/provenance validation for deploy-adjacent evidence."""
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(isinstance(receipt, dict), "receipt must be an object")
    if not isinstance(receipt, dict):
        return errors
    required_top = {
        "schema",
        "startedAtUtc",
        "completedAtUtc",
        "profile",
        "campaign",
        "config",
        "artifacts",
        "modelIdentity",
        "provenance",
        "outputDirectoryMode",
        "baseline",
        "candidate",
        "gate",
        "syntheticTenantLeakageAssertion",
        "receiptChecksumManifest",
        "boundary",
        *CLAIM_FLAGS.keys(),
    }
    require(
        required_top.issubset(receipt),
        "receipt missing required top-level fields: "
        + ", ".join(sorted(required_top - set(receipt))),
    )
    require(receipt.get("schema") == SCHEMA, "receipt schema mismatch")
    for key, expected in CLAIM_FLAGS.items():
        require(receipt.get(key) is expected, f"receipt boundary mismatch: {key}")
    require(receipt.get("outputDirectoryMode") == "0700", "output mode receipt mismatch")
    require(
        receipt.get("receiptChecksumManifest") == "sha256sums.txt",
        "checksum manifest field mismatch",
    )
    require(receipt.get("profile") in {"smoke", "long"}, "invalid receipt profile")

    campaign = receipt.get("campaign")
    require(isinstance(campaign, dict), "campaign must be an object")
    if isinstance(campaign, dict):
        shard_id = campaign.get("shardId")
        repetitions = campaign.get("repetitions")
        total_soak = campaign.get("configuredTotalSoakSeconds")
        per_arm = campaign.get("soakSecondsPerArmRun")
        aggregate = campaign.get("aggregateMeasuredSoakSeconds")
        require(
            isinstance(shard_id, int) and not isinstance(shard_id, bool) and shard_id > 0,
            "campaign shardId must be a positive integer",
        )
        require(
            isinstance(repetitions, int)
            and not isinstance(repetitions, bool)
            and repetitions > 0,
            "campaign repetitions must be a positive integer",
        )
        require(
            isinstance(total_soak, (int, float))
            and not isinstance(total_soak, bool)
            and float(total_soak) > 0,
            "configured total soak must be positive",
        )
        require(
            isinstance(per_arm, (int, float))
            and not isinstance(per_arm, bool)
            and float(per_arm) > 0,
            "per-arm soak must be positive",
        )
        require(
            isinstance(aggregate, (int, float))
            and not isinstance(aggregate, bool)
            and float(aggregate) > 0,
            "aggregate soak must be positive",
        )
        if (
            isinstance(repetitions, int)
            and repetitions > 0
            and isinstance(total_soak, (int, float))
            and isinstance(per_arm, (int, float))
        ):
            require(
                math.isclose(
                    float(per_arm) * 2 * repetitions,
                    float(total_soak),
                    rel_tol=0,
                    abs_tol=1e-6,
                ),
                "campaign soak arithmetic mismatch",
            )
        if isinstance(total_soak, (int, float)) and isinstance(
            aggregate, (int, float)
        ):
            require(
                math.isclose(
                    float(total_soak), float(aggregate), rel_tol=0, abs_tol=1e-6
                ),
                "campaign aggregate soak mismatch",
            )

    config = receipt.get("config")
    require(isinstance(config, dict), "config must be an object")
    if isinstance(config, dict):
        require(config.get("schema") == CONFIG_SCHEMA, "config schema mismatch")
        require(
            config.get("profileName") == receipt.get("profile"),
            "config profile mismatch",
        )
        require(
            isinstance(config.get("profile"), dict), "config profile must be an object"
        )
        require(isinstance(config.get("slo"), dict), "config SLO must be an object")
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(config.get("configSha256", "")))
            is not None,
            "config sha256 missing or invalid",
        )
        claims = config.get("claims")
        require(isinstance(claims, dict), "config claims must be an object")
        if isinstance(claims, dict):
            for key, expected in CLAIM_FLAGS.items():
                require(
                    claims.get(key) is expected,
                    f"config boundary mismatch: {key}",
                )

    artifacts = receipt.get("artifacts")
    require(isinstance(artifacts, dict), "artifacts must be an object")
    if isinstance(artifacts, dict):
        for arm in ("baseline", "candidate"):
            arm_artifacts = artifacts.get(arm)
            require(isinstance(arm_artifacts, dict), f"{arm} artifacts missing")
            if not isinstance(arm_artifacts, dict):
                continue
            for kind in ("server", "model"):
                identity = arm_artifacts.get(kind)
                require(
                    isinstance(identity, dict), f"{arm}.{kind} identity missing"
                )
                if not isinstance(identity, dict):
                    continue
                require(identity.get("exists") is True, f"{arm}.{kind} missing")
                require(identity.get("isFile") is True, f"{arm}.{kind} not regular")
                require(identity.get("isSymlink") is False, f"{arm}.{kind} symlink")
                require(
                    isinstance(identity.get("sizeBytes"), int)
                    and identity["sizeBytes"] > 0,
                    f"{arm}.{kind} size invalid",
                )
                require(
                    re.fullmatch(r"[0-9a-f]{64}", str(identity.get("sha256", "")))
                    is not None,
                    f"{arm}.{kind} sha256 invalid",
                )
                require(
                    isinstance(identity.get("resolvedPath"), str)
                    and bool(identity["resolvedPath"]),
                    f"{arm}.{kind} resolved path missing",
                )

    model = receipt.get("modelIdentity")
    require(isinstance(model, dict), "modelIdentity must be an object")
    if isinstance(model, dict):
        require(model.get("validated") is True, "model identity not validated")
        require(
            model.get("license") in ALLOWED_MODEL_LICENSES,
            "model license is not allowed",
        )
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(model.get("sha256", ""))) is not None,
            "model manifest sha256 invalid",
        )
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(model.get("manifestSha256", "")))
            is not None,
            "model manifest file sha256 invalid",
        )
        if isinstance(artifacts, dict):
            for arm in ("baseline", "candidate"):
                actual = (
                    artifacts.get(arm, {}).get("model", {}).get("sha256")
                    if isinstance(artifacts.get(arm), dict)
                    else None
                )
                require(
                    actual == model.get("sha256"),
                    f"{arm} model does not match manifest identity",
                )

    provenance = receipt.get("provenance")
    require(isinstance(provenance, dict), "provenance must be an object")
    if isinstance(provenance, dict):
        llama = provenance.get("llamaCpp")
        source = provenance.get("campaignSource")
        require(isinstance(llama, dict), "llama.cpp provenance missing")
        if isinstance(llama, dict):
            require(
                llama.get("allowlistedShas") == [REVIEWED_LLAMA_CPP_SHA],
                "llama.cpp allowlist mismatch",
            )
            require(
                llama.get("reviewedSha") == REVIEWED_LLAMA_CPP_SHA,
                "reviewed llama.cpp SHA mismatch",
            )
            require(
                llama.get("resolvedSha") == REVIEWED_LLAMA_CPP_SHA,
                "resolved llama.cpp SHA mismatch",
            )
            require(
                llama.get("mutableRefAccepted") is False,
                "mutable llama.cpp refs must not be accepted",
            )
        require(isinstance(source, dict), "campaign source provenance missing")
        if isinstance(source, dict):
            hosted = source.get("hostedWorkflow")
            require(isinstance(hosted, bool), "hostedWorkflow must be boolean")
            if require_hosted_workflow:
                require(hosted is True, "hosted workflow provenance is required")
                require(
                    bool(expected_source_repository),
                    "hosted verification requires expected source repository",
                )
                require(
                    bool(expected_source_ref),
                    "hosted verification requires expected source ref",
                )
                require(
                    bool(expected_source_sha),
                    "hosted verification requires expected source SHA",
                )
            require(
                isinstance(source.get("repository"), str)
                and bool(source["repository"]),
                "source repository missing",
            )
            require(
                isinstance(source.get("ref"), str) and bool(source["ref"]),
                "source ref missing",
            )
            require(
                isinstance(source.get("sha"), str) and bool(source["sha"]),
                "source sha missing",
            )
            if hosted is True:
                require(
                    source.get("ref") == "refs/heads/main",
                    "hosted source ref must be refs/heads/main",
                )
                require(
                    re.fullmatch(r"[0-9a-f]{40}", str(source.get("sha", "")))
                    is not None,
                    "hosted source SHA must be exact 40-hex",
                )
                require(
                    source.get("repository") != "local",
                    "hosted source repository cannot be local",
                )
            if expected_source_repository is not None:
                require(
                    source.get("repository") == expected_source_repository,
                    "source repository does not match expected workflow repository",
                )
            if expected_source_ref is not None:
                require(
                    source.get("ref") == expected_source_ref,
                    "source ref does not match expected workflow ref",
                )
            if expected_source_sha is not None:
                require(
                    source.get("sha") == expected_source_sha,
                    "source SHA does not match expected workflow commit",
                )

    for arm in ("baseline", "candidate"):
        aggregate_arm_receipt = receipt.get(arm)
        require(
            isinstance(aggregate_arm_receipt, dict),
            f"{arm} aggregate receipt missing",
        )
        if isinstance(aggregate_arm_receipt, dict):
            for key, expected in CLAIM_FLAGS.items():
                require(
                    aggregate_arm_receipt.get(key) is expected,
                    f"{arm} boundary mismatch: {key}",
                )
            require(
                aggregate_arm_receipt.get("tenantIsolationClaimed") is False,
                f"{arm} tenant isolation boundary mismatch",
            )
            require(
                aggregate_arm_receipt.get("foreignSentinelLeaks") == 0,
                f"{arm} foreign sentinel leak",
            )
            require(
                aggregate_arm_receipt.get("ownSentinelMissingSuccesses") == 0,
                f"{arm} nominal success omitted own sentinel",
            )

    gate = receipt.get("gate")
    require(isinstance(gate, dict), "gate must be an object")
    if isinstance(gate, dict):
        gate_verdict = gate.get("gateVerdict")
        rollback = gate.get("rollbackVerdict")
        mapping = {
            "PASS": "KEEP_CANDIDATE",
            "FAIL": "ROLLBACK_TO_BASELINE",
            "UNDETERMINED": "HOLD_UNDETERMINED",
        }
        require(gate_verdict in mapping, "invalid gate verdict")
        if gate_verdict in mapping:
            require(
                rollback == mapping[gate_verdict],
                "rollback verdict does not match gate verdict",
            )
        require(gate.get("deploymentAuthorized") is False, "deployment authorized")
        for key, expected in CLAIM_FLAGS.items():
            require(gate.get(key) is expected, f"gate boundary mismatch: {key}")
        checks = gate.get("checks")
        require(isinstance(checks, list) and bool(checks), "gate checks missing")
        if isinstance(checks, list):
            names: list[str] = []
            for index, check in enumerate(checks):
                require(isinstance(check, dict), f"gate check {index} is invalid")
                if isinstance(check, dict):
                    require(
                        isinstance(check.get("name"), str) and bool(check["name"]),
                        f"gate check {index} name missing",
                    )
                    require(
                        isinstance(check.get("passed"), bool),
                        f"gate check {index} passed is not boolean",
                    )
                    if isinstance(check.get("name"), str):
                        names.append(check["name"])
            require(len(names) == len(set(names)), "gate check names are not unique")
            if gate_verdict == "PASS":
                require(
                    all(
                        isinstance(check, dict) and check.get("passed") is True
                        for check in checks
                    ),
                    "PASS gate contains failed checks",
                )
                require(
                    gate.get("infrastructureErrors") == [],
                    "PASS gate contains infrastructure errors",
                )

    leakage = receipt.get("syntheticTenantLeakageAssertion")
    require(isinstance(leakage, dict), "leakage assertion missing")
    if isinstance(leakage, dict):
        require(
            leakage.get("tenantIsolationClaimed") is False,
            "tenant isolation must not be claimed",
        )
        require(
            leakage.get("baselineForeignSentinelLeaks") == 0,
            "baseline leakage assertion failed",
        )
        require(
            leakage.get("candidateForeignSentinelLeaks") == 0,
            "candidate leakage assertion failed",
        )
    return errors


def verify_receipt(args: argparse.Namespace) -> int:
    result = verify_checksums(args.out_dir)
    expectation_errors: list[str] = []
    if args.require_hosted_workflow:
        for value, label in (
            (args.expected_source_repository, "repository"),
            (args.expected_source_ref, "ref"),
            (args.expected_source_sha, "SHA"),
        ):
            if not value:
                expectation_errors.append(
                    f"--require-hosted-workflow requires expected source {label}"
                )
    if (
        args.expected_source_sha is not None
        and re.fullmatch(r"[0-9a-f]{40}", args.expected_source_sha) is None
    ):
        expectation_errors.append(
            "--expected-source-sha must be exact lowercase 40-hex"
        )
    if expectation_errors:
        result["valid"] = False
        result["errors"].extend(expectation_errors)
    try:
        root = args.out_dir.resolve(strict=True)
    except OSError:
        root = args.out_dir
    receipt_path = root / "receipt.json"
    if not receipt_path.is_file():
        result["valid"] = False
        result["errors"].append("receipt.json is missing")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            schema_errors = validate_full_receipt(
                receipt,
                require_hosted_workflow=args.require_hosted_workflow,
                expected_source_repository=args.expected_source_repository,
                expected_source_ref=args.expected_source_ref,
                expected_source_sha=args.expected_source_sha,
            )
            result["errors"].extend(schema_errors)
            result["receiptSchemaValid"] = not schema_errors
            if schema_errors:
                result["valid"] = False
        except (OSError, json.JSONDecodeError) as exc:
            result["valid"] = False
            result["errors"].append(f"invalid receipt.json: {exc}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run a paired production-shaped campaign")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--profile", default="smoke")
    run.add_argument("--baseline-server", type=Path, required=True)
    run.add_argument("--baseline-model", type=Path, required=True)
    run.add_argument("--candidate-server", type=Path)
    run.add_argument("--candidate-model", type=Path)
    run.add_argument("--model-id", required=True)
    run.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    run.add_argument("--llama-cpp-sha", required=True)
    run.add_argument("--resolved-llama-cpp-sha", required=True)
    run.add_argument("--hosted-workflow", action="store_true")
    run.add_argument("--source-repository", default="local")
    run.add_argument("--source-ref", default="local")
    run.add_argument("--source-sha", default="local")
    run.add_argument("--baseline-label", default="baseline")
    run.add_argument("--candidate-label", default="candidate")
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=18081)
    run.add_argument("--shard-id", type=int, default=1)
    run.add_argument("--repetitions", type=int)
    run.add_argument("--soak-seconds-total", type=float)
    run.set_defaults(func=run_campaign)

    verify = subparsers.add_parser(
        "verify", help="verify a campaign receipt and every checksummed file"
    )
    verify.add_argument("--out-dir", type=Path, required=True)
    verify.add_argument("--require-hosted-workflow", action="store_true")
    verify.add_argument("--expected-source-repository")
    verify.add_argument("--expected-source-ref")
    verify.add_argument("--expected-source-sha")
    verify.set_defaults(func=verify_receipt)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
