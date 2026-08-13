---
title: Polygraph — Arm64 cloud inference verification
description: Judge-facing static demo for Polygraph, a fail-closed verification and deployment gate for Arm64 cloud inference.
colorFrom: indigo
colorTo: gray
sdk: static
---

# Polygraph — verify Arm64 cloud inference before rollout

This Space is a static, no-backend judge demo for the [Arm Create: AI Optimization Challenge](https://arm-ai-optimization-challenge.devpost.com/).

It mirrors committed Polygraph evidence and does not run benchmarks in the browser. Start with the
portable software-mismatch walkthrough, inspect the **L1/L2/L3 method**, then read the **raw Arm
receipts** and the separate paired performance/rollback evidence.

- Source: https://github.com/tomyimkc/polygraph
- Submission: https://devpost.com/software/polygraph-4v29tr
- Demo page: https://tomyimkc-polygraph-arm-demo.static.hf.space/
- Evidence dashboard: https://tomyimkc.github.io/polygraph/
- Quickstart: https://github.com/tomyimkc/polygraph/blob/main/docs/QUICKSTART.md
- Claims gate: `python3 tools/check_claims.py`
- Judge FAQ: https://github.com/tomyimkc/polygraph/blob/main/docs/JUDGE-FAQ.md

## Claim boundary

The Arm CPU is not "lying"; Polygraph checks potentially misleading software build/runtime
signals. L1/L2/L3 proves dispatch for the measured workload, not speed. Performance is measured
separately. The page reports a configuration-specific broken-versus-corrected Arm CPU build
measurement and a later synthetic candidate rollback. It does **not** claim a new Polygraph
kernel, a GPU speedup, a stock-release defect, universal speedup, production traffic, deployment
authorization, or production readiness. The local demo demonstrates the verifier; it is not the
Arm benchmark.
