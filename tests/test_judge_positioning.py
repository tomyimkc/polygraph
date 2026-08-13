#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Polygraph contributors
"""Regression tests for the submission's judge-facing claim boundaries."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestJudgePositioning(unittest.TestCase):
    def test_readme_separates_dispatch_from_performance(self):
        text = read("README.md")
        self.assertIn("Track: Cloud AI", text)
        self.assertIn("fail-closed verification and deployment gate", text)
        self.assertIn("Dispatch is execution evidence,\nnot a speed claim", text)
        self.assertIn("broken-versus-corrected build comparison", text)
        self.assertIn("not the Arm benchmark", text)

    def test_faq_answers_core_objections(self):
        text = read("docs/JUDGE-FAQ.md")
        for required in (
            "The hardware is not the speaker",
            "Why does this fit the Cloud AI track?",
            "Does a debugger dispatch count prove a performance improvement?",
            "Is the 4.57x comparison a new Polygraph optimization?",
            "Is `make demo` the Arm contest evidence?",
            "actualProductionTraffic:false",
            "productionReady:false",
            "candidateOnly:true",
            "`-ngl 0`",
            "`ROLLBACK_TO_BASELINE`",
        ):
            self.assertIn(required, text)

    def test_devpost_source_contains_current_video_and_claim_ceiling(self):
        text = read("docs/DEVPOST-SUBMISSION.md")
        self.assertIn("https://youtu.be/er9PA5YYdzg", text)
        self.assertIn("Cloud AI fit and separation of claims", text)
        self.assertIn("did not invent a new matmul kernel", text)
        self.assertIn("explicitly starts `llama-server` with `-ngl 0`", text)
        final_block = text.split("# Final paste-ready Devpost block", 1)[1]
        self.assertIn("Track: Cloud AI", final_block)
        self.assertIn("broken-versus-corrected", final_block)
        self.assertIn("FAIL / ROLLBACK_TO_BASELINE", final_block)
        self.assertIn("Challenge-period confirmation", final_block)
        self.assertNotIn("lie detector for accelerated software", final_block)
        self.assertNotIn("built a real optimization", final_block)

    def test_static_demo_does_not_label_build_correction_as_generic_speedup(self):
        html = read("space/index.html")
        js = read("space/app.js")
        self.assertIn("corrected / broken build", html)
        self.assertIn("This proves dispatch for the measured workload—not speed or readiness", html)
        self.assertNotIn('ratio(value) + " faster"', js)

    def test_product_thesis_supersedes_historical_shorthand(self):
        text = read("docs/PRODUCT.md")
        self.assertIn("Current position — verifier and performance gate", text)
        self.assertIn("verification itself creates speed", text)


if __name__ == "__main__":
    unittest.main()
