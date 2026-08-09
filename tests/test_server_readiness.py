#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure-logic tests for tools/server_readiness.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = REPO_ROOT / "tools" / "server_readiness.py"
    spec = importlib.util.spec_from_file_location("server_readiness_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sr = _load_module()


def _request(**overrides):
    base = dict(
        schema=sr.REQUEST_SCHEMA,
        phase="soak",
        concurrency=4,
        workload="short-chat",
        startedAtUtc="2026-08-09T00:00:00+00:00",
        status="success",
        httpStatus=200,
        ttftMs=100.0,
        e2eMs=500.0,
        promptTokens=10,
        completionTokens=20,
        outputCharacters=40,
        error=None,
    )
    base.update(overrides)
    return sr.RequestResult(**base)


class TestPercentile(unittest.TestCase):
    def test_empty_and_interpolated(self):
        self.assertIsNone(sr.percentile([], 95))
        self.assertEqual(sr.percentile([10], 99), 10)
        self.assertAlmostEqual(sr.percentile([0, 100], 95), 95)

    def test_invalid_quantile(self):
        with self.assertRaises(ValueError):
            sr.percentile([1], 101)


class TestSummaries(unittest.TestCase):
    def test_success_accounting(self):
        rows = [_request(), _request(ttftMs=200, completionTokens=30)]
        summary = sr.summarize_results("soak", 4, rows, 2.0)
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["completionTokens"], 50)
        self.assertEqual(summary["outputTokensPerSecond"], 25)
        self.assertEqual(summary["usageMissingRequests"], 0)

    def test_missing_usage_is_not_hidden(self):
        summary = sr.summarize_results(
            "soak",
            4,
            [_request(status="error", completionTokens=None, promptTokens=None)],
            1.0,
        )
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["usageMissingRequests"], 1)


class TestGate(unittest.TestCase):
    def _summary(self, phase="soak", concurrency=4):
        return {
            "phase": phase,
            "concurrency": concurrency,
            "requests": 100,
            "errorRate": 0.0,
            "usageMissingRequests": 0,
            "emptyOutputRequests": 0,
            "ttftP99Ms": 200.0,
            "e2eP99Ms": 1000.0,
        }

    def test_pass_is_arm_and_all_checks_bind(self):
        gate = sr.evaluate_gate(
            machine="aarch64",
            summaries=[self._summary()],
            restarts=[{"success": True, "readySeconds": 3.0}],
            telemetry=[{"rssMiB": 512.0}],
            min_requests=50,
            max_error_rate=0,
            max_ttft_p99_ms=1000,
            max_e2e_p99_ms=5000,
            max_recovery_seconds=30,
            max_rss_mib=2048,
        )
        self.assertEqual(gate["verdict"], "PASS")
        self.assertTrue(all(c["passed"] for c in gate["checks"]))

    def test_non_arm_or_missing_restart_fails(self):
        gate = sr.evaluate_gate(
            machine="x86_64",
            summaries=[self._summary()],
            restarts=[],
            telemetry=[{"rssMiB": 512.0}],
            min_requests=50,
            max_error_rate=0,
            max_ttft_p99_ms=1000,
            max_e2e_p99_ms=5000,
            max_recovery_seconds=30,
            max_rss_mib=2048,
        )
        self.assertEqual(gate["verdict"], "FAIL")
        failed = {c["name"] for c in gate["checks"] if not c["passed"]}
        self.assertIn("arm64-architecture", failed)
        self.assertIn("restart-success", failed)

    def test_soak_latency_threshold_fails(self):
        summary = self._summary()
        summary["ttftP99Ms"] = 5001.0
        gate = sr.evaluate_gate(
            machine="arm64",
            summaries=[summary],
            restarts=[{"success": True, "readySeconds": 3.0}],
            telemetry=[{"rssMiB": 512.0}],
            min_requests=50,
            max_error_rate=0,
            max_ttft_p99_ms=5000,
            max_e2e_p99_ms=5000,
            max_recovery_seconds=30,
            max_rss_mib=2048,
        )
        self.assertEqual(gate["verdict"], "FAIL")


class TestParsing(unittest.TestCase):
    def test_concurrencies_are_positive_and_deduplicated(self):
        self.assertEqual(sr.parse_concurrencies("1,2,2,4"), [1, 2, 4])
        with self.assertRaises(Exception):
            sr.parse_concurrencies("0")


class TestStreamingIntegration(unittest.TestCase):
    def test_mock_sse_completion_retains_content_and_final_usage(self):
        requests = []

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
                    b": mock heartbeat\n\n",
                    b'data: {"content":"hello ","tokens_evaluated":7}\n\n',
                    b'data: {"content":"world","stop":true,'
                    b'"timings":{"prompt_n":7,"predicted_n":2}}\n\n',
                    b"data: [DONE]\n\n",
                )
                for event in events:
                    self.wfile.write(event)
                    self.wfile.flush()

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = sr.stream_completion(
                "127.0.0.1",
                server.server_address[1],
                "test prompt",
                24,
                5,
                phase="integration",
                concurrency=1,
                workload="mock-sse",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.httpStatus, 200)
        self.assertEqual(result.promptTokens, 7)
        self.assertEqual(result.completionTokens, 2)
        self.assertEqual(result.outputCharacters, len("hello world"))
        self.assertIsNotNone(result.ttftMs)
        self.assertIsNone(result.error)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/completion")
        self.assertEqual(requests[0]["accept"], "text/event-stream")
        self.assertTrue(requests[0]["body"]["stream"])
        self.assertEqual(requests[0]["body"]["n_predict"], 24)


if __name__ == "__main__":
    unittest.main()
