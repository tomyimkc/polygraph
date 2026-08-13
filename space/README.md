---
title: Polygraph — Arm acceleration proof
description: Judge-facing static demo for Polygraph, a fail-closed verifier for advertised versus executed AI acceleration on Arm.
colorFrom: indigo
colorTo: slate
sdk: static
---

# Polygraph — stop guessing about AI performance

This Space is a static, no-backend judge demo for the [Arm Create: AI Optimization Challenge](https://arm-ai-optimization-challenge.devpost.com/).

It mirrors committed Polygraph evidence and does not run benchmarks in the browser. Start with the **catch-a-liar walkthrough**, inspect the **L1/L2/L3 method**, then read the **raw receipts**.

- Source: https://github.com/tomyimkc/polygraph
- Submission: https://devpost.com/software/polygraph-4v29tr
- Submission walkthrough: `assets/polygraph-contest-final.mp4`
- Evidence dashboard: https://tomyimkc.github.io/polygraph/
- Quickstart: https://github.com/tomyimkc/polygraph/blob/main/docs/QUICKSTART.md
- Claims gate: `python3 tools/check_claims.py`

## Claim boundary

The page reports a configuration-specific Arm measurement and a later synthetic candidate rollback. It does **not** claim universal speedup, production traffic, deployment authorization, or production readiness.
