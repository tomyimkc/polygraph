# Polygraph Pro 6000 production-shaped serving control

Run: `31289517517`  
Artifact: `skill-lift-pro6000-polygraph-production-31289517517-1`
(`9031433314`, 7,129,774 compressed bytes at download time)  
Git SHA: `79bf3d1019b38806e22fb6f4940749e9441a555b`

## Boundary

This is production-grade **synthetic** OpenAI-compatible serving load on the
owned x86_64/CUDA RTX PRO 6000. It is not observed customer traffic and cannot
be used as Arm, KleidiAI, or contest evidence.

```text
actualProductionTraffic: false
armContestEvidence: false
candidateOnly: true
registeredResult: false
canClaimAGI: false
```

## Reproducible configuration

- Model: `Qwen/Qwen3-30B-A3B-Instruct-2507`
- Immutable revision: `0d7cf23991f47feeb3a57ecb4c9cee8ea4a17bfe`
- Model shape: Qwen3 MoE, 30,532,122,624 parameters
- Runtime: vLLM `0.25.1`, torch `2.11.0+cu130`, CUDA `13.0`
- GPU: NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 95.59 GiB VRAM
- vLLM memory utilization: `0.90`
- Context ceiling: 32,768 tokens
- Traffic mix: 50% short chat, 35% RAG-like question, 15% long summary
- Capacity sweep: concurrency 1, 4, 8, 16; 60 s warm-up plus 300 s measured
  at each point
- Soak: concurrency 8; 60 s warm-up plus 900 s measured
- One-second host/GPU telemetry

## Results

All 7,573 measured requests returned HTTP 200 with usable streamed content.
All 1,071 warm-up requests also succeeded. Token-usage accounting was present
for every measured request.

| phase | concurrency | measured requests | errors | requests/s | output tok/s | TTFT p95 | E2E p95 | GPU temp mean/max | power mean/max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capacity | 1 | 391 | 0 | 1.302 | 150.87 | 118.64 ms | 1.736 s | 69.97/79 C | 398.18/429.24 W |
| capacity | 4 | 822 | 0 | 2.721 | 309.14 | 137.87 ms | 3.369 s | 76.80/83 C | 453.08/518.44 W |
| capacity | 8 | 1,173 | 0 | 3.881 | 445.50 | 173.09 ms | 4.655 s | 80.18/87 C | 486.48/529.16 W |
| capacity | 16 | 1,721 | 0 | 5.669 | 639.23 | 252.99 ms | 6.471 s | 82.80/88 C | 515.72/598.51 W |
| soak | 8 | 3,466 | 0 | 3.840 | 442.19 | 152.15 ms | 4.682 s | 80.95/87 C | 489.23/546.54 W |

Concurrency-8 soak p99 was 188.76 ms TTFT and 4.804 s end-to-end.
Per-class soak p95 end-to-end latency was 1.304 s for short chat, 2.468 s for
RAG-like requests, and 4.829 s for long summaries.

## Operational interpretation

- **Concurrency 8 is the strongest provisional operating point tested for a
  balanced latency/throughput target.** Its 15-minute soak reproduced the
  capacity point within about 1% on output throughput, with zero request errors.
- **Concurrency 4 is the conservative point** if lower temperature and latency
  are more important than aggregate throughput.
- **Concurrency 16 is a throughput point, not the default recommendation.** It
  reached 639 output tokens/s with zero errors, but mixed-traffic p95 end-to-end
  latency rose to 6.47 s and GPU temperature reached 88 C.
- Cold model readiness required 40 endpoint polling attempts at a 10-second
  interval. A production deployment should keep this model resident or use a
  startup/readiness budget of roughly seven minutes rather than expecting fast
  scale-from-zero.

This single run does not establish multi-day reliability, availability during
failover, live-traffic correctness, multi-tenant isolation, or an application
SLO. Those require repeated runs, explicit SLO thresholds, failure injection,
and an Arm-target run for the contest path.

## Evidence

- `capacity-sweep/requests-c*.jsonl`: one row per measured request.
- `capacity-sweep/host-metrics-c*.csv`: one-second host/GPU samples.
- `soak-c8/requests-c8.jsonl`: sustained-load request receipts.
- `soak-c8/host-metrics-c8.csv`: sustained-load telemetry.
- `capacity-sweep/summary.json` and `soak-c8/summary.json`: aggregates.
- `cache-preflight.json`, `host-preflight.json`, and
  `endpoint-preflight.json`: cache, hardware, model, and real endpoint proofs.
- `vllm-server.log`: server startup, request, and shutdown log.
- `gpu-release.json`, `artifact-boundary.json`, and `workflow-receipt.json`:
  cleanup and workflow-boundary receipts.
- `sha256sums.txt`: hashes of the preserved files.
