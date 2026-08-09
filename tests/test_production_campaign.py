#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
"""Dependency-free unit tests for tools/production_campaign.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "production-readiness.json"


def _load_module():
    path = REPO_ROOT / "tools" / "production_campaign.py"
    spec = importlib.util.spec_from_file_location(
        "production_campaign_under_test", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load production campaign module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pc = _load_module()


def _config(profile="smoke", **overrides):
    return pc.load_config(CONFIG_PATH, profile, **overrides)


def _observation(text, *, status="success", error=None):
    return pc.CompletionObservation(
        status=status,
        httpStatus=200 if status == "success" else 500,
        text=text,
        ttftMs=10.0 if status == "success" else None,
        e2eMs=20.0,
        promptTokens=8,
        completionTokens=3,
        error=error,
    )


def _arm(
    *,
    throughput=10.0,
    ttft=100.0,
    e2e=500.0,
    error_rate=0.0,
    readiness_passes=1,
    replay_passes=1,
    infrastructure_errors=None,
):
    return {
        "completedRuns": 1,
        "readinessSummaries": 1,
        "readinessGatePasses": readiness_passes,
        "replayGatePasses": replay_passes,
        "measuredErrorRate": error_rate,
        "soakTtftP99WorstMs": ttft,
        "soakE2eP99WorstMs": e2e,
        "soakOutputTokensPerSecondMedian": throughput,
        "traceDigests": ["paired-trace"],
        "configuredSoakSeconds": 4.0,
        "infrastructureErrors": infrastructure_errors or [],
    }


class TestConfiguration(unittest.TestCase):
    def test_profiles_keep_exact_claim_boundary(self):
        smoke = _config("smoke")
        long_profile = _config("long")
        self.assertEqual(smoke["claims"], pc.CLAIM_FLAGS)
        self.assertEqual(long_profile["claims"], pc.CLAIM_FLAGS)
        self.assertEqual(smoke["profile"]["maxTokens"], 12)
        self.assertEqual(long_profile["profile"]["maxTokens"], 24)
        self.assertEqual(smoke["profile"]["replayMaxTokens"], 64)
        self.assertEqual(long_profile["profile"]["replayMaxTokens"], 64)
        self.assertEqual(long_profile["profile"]["soakSecondsTotal"], 18000)
        self.assertEqual(long_profile["profile"]["repetitions"], 1)

    def test_overrides_are_validated(self):
        resolved = _config(
            "smoke", repetitions_override=2, soak_seconds_override=12.5
        )
        self.assertEqual(resolved["profile"]["repetitions"], 2)
        self.assertEqual(resolved["profile"]["soakSecondsTotal"], 12.5)
        with self.assertRaises(pc.ConfigurationError):
            _config("smoke", repetitions_override=0)

    def test_claim_flags_fail_closed(self):
        raw = json.loads(CONFIG_PATH.read_text())
        raw["claims"]["productionReady"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(
                pc.ConfigurationError, "productionReady must remain exactly false"
            ):
                pc.load_config(path, "smoke")

    def test_llama_sha_is_exact_and_allowlisted(self):
        self.assertEqual(
            pc.validate_reviewed_llama_sha(pc.REVIEWED_LLAMA_CPP_SHA),
            pc.REVIEWED_LLAMA_CPP_SHA,
        )
        for value in (
            "main",
            "v1.0.0",
            "dbadb68",
            "--upload-pack=evil",
            "D" * 40,
            "a" * 40,
        ):
            with self.subTest(value=value):
                with self.assertRaises(pc.ConfigurationError):
                    pc.validate_reviewed_llama_sha(value)

    def test_campaign_rejects_mutable_or_mismatched_llama_shas(self):
        cases = (
            ("main", pc.REVIEWED_LLAMA_CPP_SHA),
            (pc.REVIEWED_LLAMA_CPP_SHA, "a" * 40),
        )
        for reviewed, resolved in cases:
            with self.subTest(reviewed=reviewed, resolved=resolved):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    out = root / "campaign"
                    with contextlib.redirect_stdout(io.StringIO()):
                        rc = pc.main(
                            [
                                "run",
                                "--config",
                                str(CONFIG_PATH),
                                "--profile",
                                "smoke",
                                "--baseline-server",
                                str(root / "missing-server"),
                                "--baseline-model",
                                str(root / "missing-model"),
                                "--model-id",
                                "qwen2.5-1.5b-q4_0",
                                "--llama-cpp-sha",
                                reviewed,
                                "--resolved-llama-cpp-sha",
                                resolved,
                                "--out-dir",
                                str(out),
                            ]
                        )
                    self.assertEqual(rc, 2)
                    receipt = json.loads((out / "receipt.json").read_text())
                    self.assertEqual(receipt["gate"]["verdict"], "UNDETERMINED")
                    self.assertIn(
                        "ConfigurationError",
                        receipt["gate"]["infrastructureError"],
                    )


class TestModelManifest(unittest.TestCase):
    def test_resolves_pinned_1_5b_identity(self):
        entry = pc.load_model_manifest_entry(
            REPO_ROOT / "scripts" / "models.txt", "qwen2.5-1.5b-q4_0"
        )
        self.assertEqual(entry["hfRepo"], "Qwen/Qwen2.5-1.5B-Instruct-GGUF")
        self.assertEqual(entry["hfFile"], "qwen2.5-1.5b-instruct-q4_0.gguf")
        self.assertEqual(
            entry["sha256"],
            "dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb",
        )
        self.assertEqual(entry["license"], "apache-2.0")
        self.assertRegex(entry["manifestSha256"], r"^[0-9a-f]{64}$")

    def test_missing_or_disallowed_model_fails_closed(self):
        with self.assertRaisesRegex(pc.ConfigurationError, "not found"):
            pc.load_model_manifest_entry(
                REPO_ROOT / "scripts" / "models.txt", "missing-model"
            )
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "models.txt"
            manifest.write_text(
                "restricted|Org/Repo|model.gguf|"
                + ("a" * 64)
                + "|other|not redistributable\n"
            )
            with self.assertRaisesRegex(pc.ConfigurationError, "not allowed"):
                pc.load_model_manifest_entry(manifest, "restricted")


class TestSyntheticTrace(unittest.TestCase):
    def test_trace_is_deterministic_sanitized_and_shard_scoped(self):
        config = _config()
        first = pc.build_synthetic_trace(config, 1, campaign_shard=1)
        again = pc.build_synthetic_trace(config, 1, campaign_shard=1)
        other_shard = pc.build_synthetic_trace(config, 1, campaign_shard=2)
        self.assertEqual(first, again)
        self.assertEqual(pc.trace_digest(first), pc.trace_digest(again))
        self.assertNotEqual(pc.trace_digest(first), pc.trace_digest(other_shard))
        self.assertTrue(all(row.containsPii is False for row in first))
        self.assertTrue(all("synthetic-tenant-" in row.prompt for row in first))
        self.assertEqual(
            [row.sequence for row in first if row.injectLoadGeneratorError], [5]
        )
        self.assertEqual(
            [row.sequence for row in first if row.injectedDelayMs], [3, 6]
        )

    def test_trace_rejects_pii_and_invalid_tenant_scope(self):
        config = _config()
        rows = pc.build_synthetic_trace(config, 1)
        bad = list(rows)
        bad[0] = pc.dataclasses.replace(
            bad[0],
            prompt=bad[0].prompt + " person@example.com",
            promptSha256=pc.hashlib.sha256(
                (bad[0].prompt + " person@example.com").encode()
            ).hexdigest(),
            containsPii=True,
        )
        with self.assertRaisesRegex(pc.ConfigurationError, "no-PII"):
            pc.validate_synthetic_trace(
                bad, config["syntheticTraffic"]["tenantLabels"]
            )


class TestReplayTransport(unittest.TestCase):
    def test_openai_chat_stream_uses_request_specific_sentinel_schema(self):
        row = pc.build_synthetic_trace(_config(), 1)[0]
        requests = []
        quoted_sentinel = json.dumps(row.sentinel)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                requests.append(
                    {
                        "path": self.path,
                        "accept": self.headers.get("Accept"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.end_headers()
                events = (
                    {
                        "id": "chatcmpl-polygraph",
                        "object": "chat.completion.chunk",
                        "created": 1786262400,
                        "model": "test-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": quoted_sentinel[:12],
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-polygraph",
                        "object": "chat.completion.chunk",
                        "created": 1786262400,
                        "model": "test-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": quoted_sentinel[12:]},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-polygraph",
                        "object": "chat.completion.chunk",
                        "created": 1786262400,
                        "model": "test-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 17,
                            "completion_tokens": 9,
                            "total_tokens": 26,
                        },
                    },
                )
                for event in events:
                    self.wfile.write(
                        b"data: "
                        + json.dumps(event, separators=(",", ":")).encode("utf-8")
                        + b"\n\n"
                    )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            observation = pc.send_replay_completion(
                host="127.0.0.1",
                port=server.server_address[1],
                prompt=row.prompt,
                sentinel=row.sentinel,
                max_tokens=24,
                timeout=5,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(observation.status, "success")
        self.assertEqual(observation.text, quoted_sentinel)
        self.assertEqual(observation.promptTokens, 17)
        self.assertEqual(observation.completionTokens, 9)
        self.assertIsNone(observation.error)
        results, restarts = pc.execute_trace(
            [row],
            sender=lambda _row: observation,
            tenant_sentinels=[row.sentinel],
            injected_error_http_status=503,
            sleep_fn=lambda _seconds: None,
            restart_at=set(),
        )
        self.assertIs(results[0]["responseContractValid"], True)
        self.assertIs(results[0]["ownSentinelObserved"], True)
        summary = pc.summarize_replay(
            results,
            restarts,
            expected_restart_cycles=0,
            slo=_config()["slo"],
        )
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["accept"], "text/event-stream")
        body = request["body"]
        self.assertEqual(body["messages"][-1], {"role": "user", "content": row.prompt})
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["max_tokens"], 24)
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["seed"], 1)
        self.assertIs(body["stream"], True)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertNotIn("prompt", body)
        self.assertNotIn("n_predict", body)
        response_format = body["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]["schema"]
        self.assertEqual(schema["const"], row.sentinel)

    def test_non_streaming_chat_content_fails_closed_unless_exact_json_string(self):
        trace = pc.build_synthetic_trace(_config(), 1)
        row = trace[0]
        foreign = trace[1].sentinel
        cases = (
            ("exact", json.dumps(row.sentinel), "pass"),
            ("missing", None, "unexpected-error"),
            ("unquoted", row.sentinel, "invalid-contract"),
            ("malformed-json", f'"{row.sentinel}', "invalid-contract"),
            (
                "wrong-json-shape",
                json.dumps({"sentinel": row.sentinel}),
                "invalid-contract",
            ),
            ("foreign", json.dumps(foreign), "foreign"),
        )

        for label, content, expected in cases:
            with self.subTest(label=label):
                message = {"role": "assistant"}
                if content is not None:
                    message["content"] = content
                payload = {
                    "id": f"chatcmpl-{label}",
                    "object": "chat.completion",
                    "created": 1786262400,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 2,
                        "total_tokens": 13,
                    },
                }

                class FakeResponse:
                    status = 200

                    @staticmethod
                    def getheader(name, default=""):
                        if name.lower() == "content-type":
                            return "application/json"
                        return default

                    @staticmethod
                    def read(_limit=None):
                        return json.dumps(payload).encode("utf-8")

                class FakeConnection:
                    def __init__(self, *_args, **_kwargs):
                        pass

                    def request(self, *_args, **_kwargs):
                        pass

                    @staticmethod
                    def getresponse():
                        return FakeResponse()

                    def close(self):
                        pass

                with mock.patch.object(
                    pc.http.client, "HTTPConnection", FakeConnection
                ):
                    observation = pc.send_replay_completion(
                        host="127.0.0.1",
                        port=8080,
                        prompt=row.prompt,
                        sentinel=row.sentinel,
                        max_tokens=24,
                        timeout=5,
                    )

                self.assertEqual(observation.httpStatus, 200)
                results, restarts = pc.execute_trace(
                    [row],
                    sender=lambda _row: observation,
                    tenant_sentinels=sorted({item.sentinel for item in trace}),
                    injected_error_http_status=503,
                    sleep_fn=lambda _seconds: None,
                    restart_at=set(),
                )
                summary = pc.summarize_replay(
                    results,
                    restarts,
                    expected_restart_cycles=0,
                    slo=_config()["slo"],
                )

                if expected == "pass":
                    self.assertEqual(observation.status, "success")
                    self.assertIsNone(observation.error)
                    self.assertIs(results[0]["responseContractValid"], True)
                    self.assertIs(results[0]["ownSentinelObserved"], True)
                    self.assertEqual(summary["verdict"], "PASS")
                elif expected == "unexpected-error":
                    self.assertEqual(observation.status, "error")
                    self.assertIsNotNone(observation.error)
                    self.assertEqual(results[0]["status"], "unexpected-error")
                    self.assertEqual(summary["unexpectedErrors"], 1)
                    self.assertEqual(summary["verdict"], "FAIL")
                elif expected == "invalid-contract":
                    self.assertEqual(observation.status, "success")
                    self.assertIs(results[0]["responseContractValid"], False)
                    self.assertIs(results[0]["ownSentinelObserved"], False)
                    self.assertEqual(summary["responseContractInvalidSuccesses"], 1)
                    self.assertEqual(summary["ownSentinelMissingSuccesses"], 1)
                    self.assertEqual(summary["verdict"], "FAIL")
                else:
                    self.assertEqual(expected, "foreign")
                    self.assertEqual(observation.status, "success")
                    self.assertIs(results[0]["responseContractValid"], True)
                    self.assertIs(results[0]["ownSentinelObserved"], False)
                    self.assertEqual(results[0]["foreignSentinels"], [foreign])
                    self.assertEqual(summary["foreignSentinelLeaks"], 1)
                    self.assertEqual(summary["ownSentinelMissingSuccesses"], 1)
                    self.assertEqual(summary["verdict"], "FAIL")


class TestReplay(unittest.TestCase):
    def test_injections_do_not_reach_sender_and_restart_is_controlled(self):
        config = _config()
        trace = pc.build_synthetic_trace(config, 1)
        sent = []
        sleeps = []
        restarts = []

        def sender(row):
            sent.append(row.sequence)
            return _observation(json.dumps(row.sentinel))

        def restart_hook(sequence):
            restarts.append(sequence)
            return {
                "success": True,
                "readySeconds": 0.2,
                "modelReloaded": True,
            }

        results, events = pc.execute_trace(
            trace,
            sender=sender,
            tenant_sentinels=sorted({row.sentinel for row in trace}),
            injected_error_http_status=503,
            sleep_fn=sleeps.append,
            restart_at={4},
            restart_hook=restart_hook,
        )
        self.assertNotIn(5, sent)
        self.assertEqual(len(sent), len(trace) - 1)
        self.assertEqual(sleeps, [0.025, 0.025])
        self.assertEqual(restarts, [4])
        self.assertTrue(events[0]["success"])
        injected = [row for row in results if row["status"] == "injected-error"]
        self.assertEqual(len(injected), 1)
        self.assertFalse(injected[0]["requestSentToServer"])

        summary = pc.summarize_replay(
            results,
            events,
            expected_restart_cycles=1,
            slo=config["slo"],
        )
        self.assertEqual(summary["verdict"], "PASS")
        self.assertFalse(summary["tenantIsolationClaimed"])

    def test_foreign_sentinel_fails_leakage_assertion(self):
        config = _config()
        trace = pc.build_synthetic_trace(config, 1)
        foreign = trace[1].sentinel

        def sender(row):
            if row.sequence == 1:
                return _observation(json.dumps(foreign))
            return _observation(json.dumps(row.sentinel))

        results, events = pc.execute_trace(
            trace,
            sender=sender,
            tenant_sentinels=sorted({row.sentinel for row in trace}),
            injected_error_http_status=503,
            sleep_fn=lambda _seconds: None,
            restart_at=set(),
        )
        summary = pc.summarize_replay(
            results,
            events,
            expected_restart_cycles=0,
            slo=config["slo"],
        )
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["foreignSentinelLeaks"], 1)

    def test_nominal_success_without_own_sentinel_fails(self):
        config = _config()
        trace = pc.build_synthetic_trace(config, 1)

        def sender(row):
            if row.sequence == 1:
                return _observation(
                    "synthetic response without the required canary"
                )
            return _observation(json.dumps(row.sentinel))

        results, events = pc.execute_trace(
            trace,
            sender=sender,
            tenant_sentinels=sorted({row.sentinel for row in trace}),
            injected_error_http_status=503,
            sleep_fn=lambda _seconds: None,
            restart_at=set(),
        )
        summary = pc.summarize_replay(
            results,
            events,
            expected_restart_cycles=0,
            slo=config["slo"],
        )
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["ownSentinelMissingSuccesses"], 1)

    def test_restart_positions_are_deterministic(self):
        self.assertEqual(pc.restart_positions(18, 2), {6, 13})
        self.assertEqual(pc.restart_positions(8, 1), {4})
        self.assertEqual(pc.restart_positions(8, 0), set())


class TestRollbackVerdict(unittest.TestCase):
    def test_keep_candidate_when_all_paired_checks_pass(self):
        comparison = pc.compare_candidate(
            _arm(),
            _arm(throughput=9.0, ttft=110.0, e2e=550.0),
            repetitions=1,
            configured_total_soak_seconds=8,
            slo=_config()["slo"],
            same_artifact_control=False,
        )
        self.assertEqual(comparison["rollbackVerdict"], "KEEP_CANDIDATE")
        self.assertEqual(comparison["gateVerdict"], "PASS")
        self.assertFalse(comparison["deploymentAuthorized"])

    def test_candidate_regression_rolls_back(self):
        comparison = pc.compare_candidate(
            _arm(),
            _arm(throughput=0.01, ttft=100.0, e2e=500.0),
            repetitions=1,
            configured_total_soak_seconds=8,
            slo=_config()["slo"],
            same_artifact_control=False,
        )
        self.assertEqual(comparison["rollbackVerdict"], "ROLLBACK_TO_BASELINE")
        self.assertEqual(comparison["gateVerdict"], "FAIL")

    def test_invalid_baseline_holds_undetermined(self):
        comparison = pc.compare_candidate(
            _arm(readiness_passes=0),
            _arm(),
            repetitions=1,
            configured_total_soak_seconds=8,
            slo=_config()["slo"],
            same_artifact_control=False,
        )
        self.assertEqual(comparison["rollbackVerdict"], "HOLD_UNDETERMINED")
        self.assertEqual(comparison["gateVerdict"], "UNDETERMINED")


class TestReceipts(unittest.TestCase):
    def test_output_directory_is_new_private_and_not_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign"
            out = pc.prepare_output_dir(target)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
            with self.assertRaisesRegex(pc.ConfigurationError, "must be new"):
                pc.prepare_output_dir(target)

    def test_checksums_detect_tampering_and_unlisted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "receipt.json").write_text("{}\n")
            nested = out / "nested"
            nested.mkdir()
            (nested / "row.jsonl").write_text('{"ok":true}\n')
            pc.write_checksums(out)
            self.assertTrue(pc.verify_checksums(out)["valid"])
            (nested / "row.jsonl").write_text('{"ok":false}\n')
            result = pc.verify_checksums(out)
            self.assertFalse(result["valid"])
            self.assertIn("checksum mismatch: nested/row.jsonl", result["errors"])

    def test_checksums_reject_symlinks_and_outside_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_text("outside\n")
            out = pc.prepare_output_dir(root / "campaign")
            (out / "receipt.json").write_text("{}\n")
            (out / "escape").symlink_to(outside)
            with self.assertRaisesRegex(pc.ConfigurationError, "symlink file"):
                pc.write_checksums(out)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = pc.prepare_output_dir(root / "campaign")
            (out / "receipt.json").write_text("{}\n")
            pc.write_checksums(out)
            outside = root / "outside.txt"
            outside.write_text("outside\n")
            (out / "late-link").symlink_to(outside)
            result = pc.verify_checksums(out)
            self.assertFalse(result["valid"])
            self.assertTrue(
                any("symlink file is forbidden" in error for error in result["errors"])
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = pc.prepare_output_dir(root / "campaign")
            outside = root / "outside.txt"
            outside.write_text("outside\n")
            digest = pc.sha256_file(outside)
            (out / "sha256sums.txt").write_text(f"{digest}  ../outside.txt\n")
            result = pc.verify_checksums(out)
            self.assertFalse(result["valid"])
            self.assertIn("unsafe checksum path on line 1", result["errors"])

    def test_artifact_drift_guard_holds_on_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "llama-server"
            server.write_bytes(b"server-v1")
            model = root / "model.gguf"
            model.write_bytes(b"model-v1")
            guard = pc.ArtifactDriftGuard(
                server=server,
                model=model,
                expected_server=pc._artifact_identity(server),
                expected_model=pc._artifact_identity(model),
            )
            guard.snapshot("before")
            model.write_bytes(b"model-v2")
            guard.snapshot("after", fail=False)
            receipt = guard.receipt()
            self.assertEqual(receipt["verdict"], "UNDETERMINED")
            self.assertTrue(any("model sha256 drift" in e for e in receipt["errors"]))

    def test_readiness_rehashes_around_each_start_stop_and_restart(self):
        class FakeController:
            def __init__(self, **_kwargs):
                pass

            def start(self, _label):
                return 0.01

            def stop(self):
                return {"exitCode": 0, "forced": False}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "llama-server"
            server.write_bytes(b"server")
            model = root / "model.gguf"
            model.write_bytes(b"model")
            guard = pc.ArtifactDriftGuard(
                server=server,
                model=model,
                expected_server=pc._artifact_identity(server),
                expected_model=pc._artifact_identity(model),
            )

            def fake_readiness_main(_args):
                controller = pc.readiness.ServerController()
                controller.start("cold-start")
                controller.stop()
                controller.start("restart-1")
                controller.stop()
                return 2

            with mock.patch.object(
                pc.readiness, "ServerController", FakeController
            ), mock.patch.object(pc.readiness, "main", fake_readiness_main):
                pc.run_readiness_measurement(
                    server=server,
                    model=model,
                    out_dir=root / "arm",
                    host="127.0.0.1",
                    port=18081,
                    profile=_config()["profile"],
                    slo=_config()["slo"],
                    soak_seconds=1,
                    artifact_guard=guard,
                )
            labels = {row["label"] for row in guard.snapshots}
            for expected in (
                "readiness-arm:before",
                "readiness:cold-start:before-start",
                "readiness:cold-start:after-start",
                "readiness:before-stop",
                "readiness:after-stop",
                "readiness:restart-1:before-start",
                "readiness:restart-1:after-start",
                "readiness-arm:after",
            ):
                self.assertIn(expected, labels)
            self.assertEqual(guard.receipt()["verdict"], "PASS")

    def test_missing_artifacts_emit_fail_closed_boundary_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "campaign"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = pc.main(
                    [
                        "run",
                        "--config",
                        str(CONFIG_PATH),
                        "--profile",
                        "smoke",
                        "--baseline-server",
                        str(Path(tmp) / "missing-server"),
                        "--baseline-model",
                        str(Path(tmp) / "missing-model"),
                        "--model-id",
                        "qwen2.5-1.5b-q4_0",
                        "--llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--resolved-llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--out-dir",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 2)
            receipt = json.loads((out / "receipt.json").read_text())
            self.assertIs(receipt["actualProductionTraffic"], False)
            self.assertIs(receipt["productionReady"], False)
            self.assertIs(receipt["candidateOnly"], True)
            self.assertEqual(receipt["gate"]["verdict"], "UNDETERMINED")
            self.assertTrue(pc.verify_checksums(out)["valid"])

    def test_end_to_end_orchestration_with_pure_fakes(self):
        readiness_summary = {
            "gate": {"verdict": "PASS"},
            "totals": {"measuredRequests": 10, "measuredFailures": 0},
            "results": [
                {
                    "phase": "soak",
                    "ttftP99Ms": 100.0,
                    "e2eP99Ms": 500.0,
                    "outputTokensPerSecond": 10.0,
                }
            ],
            "restarts": [{"readySeconds": 0.5, "success": True}],
            **pc.CLAIM_FLAGS,
        }
        readiness_receipt = {
            "exitCode": 0,
            "error": None,
            "summary": readiness_summary,
            **pc.CLAIM_FLAGS,
        }
        replay_receipt = {
            "verdict": "PASS",
            "foreignSentinelLeaks": 0,
            "unexpectedErrors": 0,
            "expectedInjectedErrors": 1,
            "observedInjectedErrors": 1,
            "restartEvents": [{"readySeconds": 0.25, "success": True}],
            **pc.CLAIM_FLAGS,
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "llama-server"
            server.write_text("#!/bin/sh\nexit 0\n")
            server.chmod(0o755)
            model = root / "model.gguf"
            model.write_bytes(b"GGUF synthetic unit fixture")
            model_sha = pc.sha256_file(model)
            manifest = root / "models.txt"
            manifest.write_text(
                "synthetic-fixture|Example/Fixture|model.gguf|"
                f"{model_sha}|apache-2.0|unit-only generated fixture\n"
            )
            out = root / "campaign"
            with mock.patch.object(
                pc, "run_readiness_measurement", return_value=readiness_receipt
            ), mock.patch.object(
                pc, "run_replay_measurement", return_value=replay_receipt
            ), contextlib.redirect_stdout(
                io.StringIO()
            ):
                rc = pc.main(
                    [
                        "run",
                        "--config",
                        str(CONFIG_PATH),
                        "--profile",
                        "smoke",
                        "--baseline-server",
                        str(server),
                        "--baseline-model",
                        str(model),
                        "--model-id",
                        "synthetic-fixture",
                        "--model-manifest",
                        str(manifest),
                        "--llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--resolved-llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--out-dir",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 0)
            receipt = json.loads((out / "receipt.json").read_text())
            self.assertEqual(receipt["gate"]["gateVerdict"], "PASS")
            self.assertEqual(receipt["gate"]["rollbackVerdict"], "KEEP_CANDIDATE")
            self.assertTrue(receipt["gate"]["sameArtifactControl"])
            self.assertEqual(
                receipt["modelIdentity"]["modelId"], "synthetic-fixture"
            )
            self.assertEqual(receipt["modelIdentity"]["sha256"], model_sha)
            self.assertEqual(receipt["modelIdentity"]["license"], "apache-2.0")
            self.assertTrue(receipt["modelIdentity"]["validated"])
            self.assertEqual(receipt["campaign"]["aggregateMeasuredSoakSeconds"], 8)
            self.assertEqual(
                receipt["campaign"]["longestContinuousSoakSegmentSeconds"], 4
            )
            self.assertTrue(pc.verify_checksums(out)["valid"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    pc.main(["verify", "--out-dir", str(out)]),
                    0,
                )

            expected_repository = "tomyimkc/polygraph"
            expected_ref = "refs/heads/main"
            expected_sha = "a" * 40
            receipt["provenance"]["campaignSource"] = {
                "hostedWorkflow": True,
                "repository": expected_repository,
                "ref": expected_ref,
                "sha": expected_sha,
            }
            (out / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
            pc.write_checksums(out)
            hosted_verify_args = [
                "verify",
                "--out-dir",
                str(out),
                "--require-hosted-workflow",
                "--expected-source-repository",
                expected_repository,
                "--expected-source-ref",
                expected_ref,
                "--expected-source-sha",
                expected_sha,
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pc.main(hosted_verify_args), 0)

            receipt["provenance"]["campaignSource"]["sha"] = "b" * 40
            (out / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
            pc.write_checksums(out)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pc.main(hosted_verify_args), 1)

            receipt["provenance"]["campaignSource"]["sha"] = expected_sha
            receipt["provenance"]["campaignSource"]["repository"] = (
                "attacker/other-repository"
            )
            (out / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
            pc.write_checksums(out)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pc.main(hosted_verify_args), 1)

            receipt["provenance"]["campaignSource"]["repository"] = (
                expected_repository
            )
            receipt["gate"]["rollbackVerdict"] = "ROLLBACK_TO_BASELINE"
            (out / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
            pc.write_checksums(out)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    pc.main(["verify", "--out-dir", str(out)]),
                    1,
                )

    def test_model_sha_mismatch_holds_before_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "llama-server"
            server.write_text("#!/bin/sh\nexit 0\n")
            server.chmod(0o755)
            model = root / "model.gguf"
            model.write_bytes(b"GGUF wrong bytes")
            manifest = root / "models.txt"
            manifest.write_text(
                "synthetic-fixture|Example/Fixture|model.gguf|"
                + ("a" * 64)
                + "|mit|unit-only generated fixture\n"
            )
            out = root / "campaign"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = pc.main(
                    [
                        "run",
                        "--config",
                        str(CONFIG_PATH),
                        "--profile",
                        "smoke",
                        "--baseline-server",
                        str(server),
                        "--baseline-model",
                        str(model),
                        "--model-id",
                        "synthetic-fixture",
                        "--model-manifest",
                        str(manifest),
                        "--llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--resolved-llama-cpp-sha",
                        pc.REVIEWED_LLAMA_CPP_SHA,
                        "--out-dir",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 2)
            receipt = json.loads((out / "receipt.json").read_text())
            self.assertFalse(receipt["modelIdentity"]["validated"])
            self.assertIn(
                "model sha256 does not match manifest",
                receipt["gate"]["infrastructureError"],
            )


class TestWorkflowContract(unittest.TestCase):
    def test_workflow_is_main_only_immutable_and_1_5b_default(self):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "verify-production-arm64.yml"
        ).read_text()
        self.assertIn("default: qwen2.5-1.5b-q4_0", workflow)
        self.assertIn(
            'MODEL_PATH=$cache_root/models/$MODEL_FILE',
            workflow,
        )
        self.assertIn('MODEL_ID="$PRODUCTION_MODEL_ID"', workflow)
        self.assertIn(
            "long production evidence requires qwen2.5-1.5b-q4_0",
            workflow,
        )
        self.assertNotIn("baseline_llama_ref", workflow)
        self.assertNotIn("candidate_llama_ref", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s+llama.*ref:")
        self.assertNotIn("assert ", workflow)
        self.assertIn('refs/heads/main', workflow)
        self.assertIn(pc.REVIEWED_LLAMA_CPP_SHA, workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("sorted(shard_ids) != expected_ids", workflow)
        self.assertIn("Capture reproducible build provenance", workflow)
        self.assertIn("CMakeCache.txt", workflow)
        self.assertIn("llama-submodules.txt", workflow)
        self.assertIn("toolchain-and-artifacts.txt", workflow)
        self.assertIn("Upload complete build provenance", workflow)
        self.assertIn("polygraph-production-build-provenance-", workflow)
        self.assertIn("--require-hosted-workflow", workflow)
        self.assertIn("--expected-source-repository", workflow)
        self.assertIn("--expected-source-ref", workflow)
        self.assertIn("--expected-source-sha", workflow)
        wrapper = (
            REPO_ROOT / "scripts" / "lib" / "run_production_campaign.sh"
        ).read_text()
        self.assertIn("10#$PRODUCTION_PORT >= 1", wrapper)
        self.assertIn("10#$PRODUCTION_PORT <= 65535", wrapper)

        uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
        self.assertTrue(uses)
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        expected_actions = {
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "actions/cache@caa296126883cff596d87d8935842f9db880ef25",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        }
        self.assertEqual(set(uses), expected_actions)
        self.assertEqual(
            workflow.count("persist-credentials: false"),
            workflow.count("uses: actions/checkout@"),
        )
        self.assertNotIn("Cache reviewed llama.cpp build", workflow)
        self.assertIn("llama.cpp build directory must start absent", workflow)
        self.assertIn("https://github.com/ggml-org/llama.cpp.git", workflow)
        self.assertIn("model_manifest_sha256 }}-v3", workflow)


if __name__ == "__main__":
    unittest.main()
