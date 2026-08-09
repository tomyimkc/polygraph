## Cloud-class throughput -- aarch64 (verify-free-arm64, Neoverse-N2 class)

- Generated: 2026-08-09T04:27:50Z
- Model: qwen2.5-0.5b-instruct-q4_0.gguf
- Reps requested per cell: 5
- NEON/i8mm-only platform: Neoverse-N2's SVE2 is 128-bit, below KleidiAI's 256-bit SVE dispatch gate, and it has no SME2 at all (see results/GROUND-TRUTH-DISPATCH.md, Finding 2). These are cloud-class Arm64 baseline throughput and thread-scaling numbers, not an SME2 result.
- Round-robin interleaved (phase x threads, one pass per round), median/stddev/min/max across rounds -- never a bare mean.

| phase | threads | median tok/s | stddev | min | max | n/reps |
|---|---:|---:|---:|---:|---:|---:|
| decode | 1 | 41.0 | 0.29 | 40.5 | 41.3 | 5/5 |
| decode | 2 | 70.1 | 0.62 | 69.9 | 71.4 | 5/5 |
| decode | 4 | 119.8 | 1.84 | 118.6 | 122.6 | 5/5 |
| prefill | 1 | 149.1 | 0.05 | 149.0 | 149.2 | 5/5 |
| prefill | 2 | 275.5 | 0.11 | 275.3 | 275.6 | 5/5 |
| prefill | 4 | 484.9 | 2.20 | 480.4 | 486.0 | 5/5 |

