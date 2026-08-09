# Arm64 production-readiness and contest evidence

GitHub Actions run: `31294460364`  
Workflow: `verify-free-arm64.yml`  
Run commit: `97f1c210dc0e3435d04f8082a362e291d409a261`  
Measured: 2026-08-09  
Runner: GitHub-hosted `ubuntu-24.04-arm`, `aarch64`, 4 CPUs  
Model: `qwen2.5-1.5b-instruct-q4_0.gguf`  
Pinned model SHA-256:
`dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb`

## Verdict and boundary

The configured synthetic readiness gate returned **PASS**, and the artifact marks
itself as an Arm contest-evidence candidate.

It still says:

```text
actualProductionTraffic: false
productionReady: false
candidateOnly: true
```

This is a production-shaped, single-host synthetic measurement. It does not prove
multi-day availability, failover, multi-tenant isolation, live-traffic correctness, or
an application SLO.

## Workload

- Pinned `llama.cpp` commit:
  `dbadb68eecdfb3ab0e86872d011738fc937f0364`
- CPU-only `llama-server`: `-ngl 0`
- Continuous batching: `-cb`
- Server slots: 4
- Threads / batch threads: 4 / 4
- Context: 2,048 tokens
- Output cap: 24 tokens per request
- Capacity sweep: concurrency 1, 2, and 4 for 30 measured seconds each
- Sustained phase: concurrency 4 for 600 measured seconds
- Traffic families: short chat, RAG-like prompts, longer summaries
- Controlled restart cycles: 2

## Results

| phase | concurrency | requests | failures | requests/s | output tok/s | TTFT p99 | E2E p99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| capacity | 1 | 35 | 0 | 1.148 | 27.55 | 451.80 ms | 1,139.58 ms |
| capacity | 2 | 40 | 0 | 1.284 | 30.82 | 649.43 ms | 1,964.69 ms |
| capacity | 4 | 80 | 0 | 2.616 | 62.79 | 929.91 ms | 1,801.08 ms |
| soak | 4 | 1,388 | 0 | 2.310 | 55.45 | 748.46 ms | 1,924.57 ms |

Totals: 1,543 measured requests, 1,543 successes, 0 failures, plus 11 successful
warm-up requests.

## Explicit readiness gate

| check | limit | observed | result |
|---|---:|---:|---|
| Arm64 architecture | `aarch64` or `arm64` | `aarch64` | PASS |
| Minimum measured requests | at least 50 | 1,543 | PASS |
| Error rate | at most 0 | 0 | PASS |
| Missing usage accounting | 0 | 0 | PASS |
| Empty output responses | 0 | 0 | PASS |
| Soak TTFT p99 | at most 5,000 ms | 748.46 ms | PASS |
| Soak E2E p99 | at most 30,000 ms | 1,924.57 ms | PASS |
| Failed restart cycles | 0 | 0 | PASS |
| Maximum restart readiness | at most 120 s | 1.5123 s | PASS |
| Maximum server RSS | at most 8,192 MiB | 1,869.69 MiB | PASS |

Cold readiness was 1.5049 seconds. Both controlled shutdowns exited cleanly without
forced termination, and both restarted servers passed their canary requests.

## Contest matrix from the same run

`contest-matrix/` preserves model-specific evidence extracted from the other three
successful jobs in the workflow run:

- Qwen2.5 0.5B Q4_0
- Qwen2.5 0.5B Q8_0
- Qwen2.5 1.5B Q4_0

Each model directory retains:

- the L1/L2/L3 dispatch ledger;
- hardware capture;
- the five-repetition round-robin cloud-throughput JSON and Markdown;
- the raw dispatch, throughput, and correctness logs.

All six dispatch configurations per model completed with an
`I8MM_HYBRID_DISPATCH` verdict. The matrix is supporting Arm optimization evidence;
the readiness verdict itself comes only from `summary.json`.

## Evidence inventory

- `summary.json`: authoritative aggregate, gate checks, restarts, and claim boundary.
- `measured-requests.jsonl`: one row per measured request.
- `warmup-requests.jsonl`: retained warm-up request rows.
- `telemetry.csv`: one-second server/host samples.
- `llama-server.log`: startup, request, restart, and shutdown log.
- `sha256sums.txt`: original uploaded readiness-artifact checksums.
- `workflow-receipt.json`: run, job, commit, and artifact metadata.
- `validation-receipt.json`: independent row-count, checksum, telemetry, restart,
  workflow, and contest-matrix consistency checks.
- `contest-matrix/`: cleaned, model-specific Arm dispatch and throughput evidence.
- `package-sha256sums.txt`: checksum manifest for this complete committed package.
