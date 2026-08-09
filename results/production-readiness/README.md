# Polygraph production-readiness evidence portfolio

This directory preserves two complementary production-shaped serving lanes plus a longer
same-artifact Arm64 temporal-control campaign.
It does **not** assert that Polygraph or either serving stack is production-ready.

```text
actualProductionTraffic: false
productionReady: false
candidateOnly: true
```

## Why this is not toy-only evidence

| lane | architecture | model | measured requests | failures | sustained phase | role |
|---|---|---|---:|---:|---|---|
| [`arm64-31294460364/`](arm64-31294460364/) | `aarch64`, GitHub-hosted Arm64 | Qwen2.5 1.5B Q4_0 | 1,543 | 0 | 10-minute concurrency-4 soak | Reproducible Arm contest candidate with explicit SLO and restart gates |
| [`arm64-campaign-31312308726-31312723300/`](arm64-campaign-31312308726-31312723300/) | `aarch64`, 2 GitHub-hosted Arm64 shards | Qwen2.5 1.5B Q4_0 | 79,684 | 0 | 36,000 aggregate measured seconds; 9,000-second longest continuous segment | Same-artifact temporal control with strict receipts; not an uplift claim |
| [`pro6000-31289517517/`](pro6000-31289517517/) | `x86_64` / CUDA, RTX PRO 6000 | Qwen3 30B-A3B | 7,573 | 0 | 15-minute concurrency-8 soak | Larger-model, higher-load production-shaped control; not Arm contest evidence |

The 1.5B Arm lane is deliberately small enough for a judge to reproduce on a free
four-core Arm64 runner. The 30B Pro 6000 lane establishes that the serving/load
instrumentation is not limited to a sub-billion-parameter demo. The two runs answer
different questions and must not be merged into one architecture or performance claim.

The longer Arm64 campaign answers a third question: whether the deterministic replay,
same-artifact comparison, fault-injection, rollback-decision, and provenance machinery remains
internally consistent across a larger synthetic exposure. Its baseline and candidate labels used
the same server and model hashes. The small temporal differences are therefore not an optimization
comparison and do not support an uplift claim.

## What the Arm lane establishes

- The exact branch commit built and served successfully on `aarch64`.
- Mixed short-chat, RAG-like, and longer-summary requests completed under capacity
  and sustained concurrent load.
- Every measured response retained streamed TTFT, end-to-end latency, prompt-token
  usage, completion-token usage, and non-empty output checks.
- One-second server RSS/load/thermal telemetry was retained.
- Two controlled stop/start cycles returned to readiness and passed a canary request.
- All configured readiness checks passed.
- The same workflow run also produced three model-specific Arm64 L1/L2/L3 dispatch
  ledgers and five-repetition cloud-throughput sweeps.

## What the Pro 6000 lane establishes

- A pinned public 30B-A3B model was served with vLLM on an owned RTX PRO 6000.
- The run covered concurrency 1, 4, 8, and 16 capacity points plus a sustained
  concurrency-8 soak.
- All measured request rows and one-second host/GPU telemetry are preserved with a
  SHA-256 manifest.

That lane is supporting production-shaped systems evidence only. It is explicitly
`armContestEvidence:false`.

## What remains before a production-ready claim

None of these campaigns establishes:

1. live or sanitized production traffic;
2. multi-day continuous availability — the longer campaign has 10 aggregate measured hours, but
   its longest continuous segment is 2.5 hours;
3. failover during host, process, model, or network faults;
4. multi-tenant isolation, authentication, abuse controls, or security review;
5. application-specific output correctness and customer SLOs;
6. autoscaling, alerting, capacity forecasting, deployment-integrated rollback, or cost targets.

A production-ready claim requires those application and operational controls in addition
to synthetic serving evidence.
