## Cloud-class throughput -- aarch64 (verify-free-arm64, Neoverse-N2 class)

- Generated: 2026-08-09T04:31:23Z
- Model: qwen2.5-1.5b-instruct-q4_0.gguf
- Reps requested per cell: 5
- NEON/i8mm-only platform: Neoverse-N2's SVE2 is 128-bit, below KleidiAI's 256-bit SVE dispatch gate, and it has no SME2 at all (see results/GROUND-TRUTH-DISPATCH.md, Finding 2). These are cloud-class Arm64 baseline throughput and thread-scaling numbers, not an SME2 result.
- Round-robin interleaved (phase x threads, one pass per round), median/stddev/min/max across rounds -- never a bare mean.

| phase | threads | median tok/s | stddev | min | max | n/reps |
|---|---:|---:|---:|---:|---:|---:|
| decode | 1 | 10.9 | 0.06 | 10.8 | 10.9 | 5/5 |
| decode | 2 | 19.5 | 0.05 | 19.5 | 19.6 | 5/5 |
| decode | 4 | 35.8 | 0.19 | 35.5 | 35.9 | 5/5 |
| prefill | 1 | 48.2 | 0.09 | 48.0 | 48.2 | 5/5 |
| prefill | 2 | 93.3 | 0.17 | 93.1 | 93.5 | 5/5 |
| prefill | 4 | 165.2 | 0.27 | 164.7 | 165.3 | 5/5 |

