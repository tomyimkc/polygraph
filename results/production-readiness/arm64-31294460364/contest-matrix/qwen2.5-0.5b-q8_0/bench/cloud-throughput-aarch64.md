## Cloud-class throughput -- aarch64 (verify-free-arm64, Neoverse-N2 class)

- Generated: 2026-08-09T04:29:03Z
- Model: qwen2.5-0.5b-instruct-q8_0.gguf
- Reps requested per cell: 5
- NEON/i8mm-only platform: Neoverse-N2's SVE2 is 128-bit, below KleidiAI's 256-bit SVE dispatch gate, and it has no SME2 at all (see results/GROUND-TRUTH-DISPATCH.md, Finding 2). These are cloud-class Arm64 baseline throughput and thread-scaling numbers, not an SME2 result.
- Round-robin interleaved (phase x threads, one pass per round), median/stddev/min/max across rounds -- never a bare mean.

| phase | threads | median tok/s | stddev | min | max | n/reps |
|---|---:|---:|---:|---:|---:|---:|
| decode | 1 | 41.0 | 0.53 | 40.1 | 41.5 | 5/5 |
| decode | 2 | 73.4 | 1.13 | 72.7 | 75.5 | 5/5 |
| decode | 4 | 126.7 | 21.53 | 79.6 | 130.2 | 5/5 |
| prefill | 1 | 228.6 | 0.17 | 228.5 | 228.8 | 5/5 |
| prefill | 2 | 410.7 | 0.54 | 409.8 | 411.0 | 5/5 |
| prefill | 4 | 684.3 | 0.39 | 683.5 | 684.4 | 5/5 |

