<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Polygraph contributors -->

# Contest demo shot list — authoritative 2:49 target

**Single source of truth:** [`contest-video/story.json`](contest-video/story.json).
`tools/render_contest_video.py` renders these six fixed-duration beats and
rejects any final MP4 whose duration is not strictly below 180 seconds.

This is the **single authoritative submission cut**. No footage or measured
output is invented. The cards load numbers from cited repository files; Beat 4
includes receipt-bound project-function footage from an actual `make demo` run.

Final post-hardening sidecar values: **169.021333 seconds**, **9,527,083
bytes**, SHA-256
**`076de488a8939f56278655a0775775257c7537317697aa3003d4aaf380ed18e4`**.
The validation sidecar remains the authoritative machine-readable source.

| # | Time | Story job | Load-bearing visual | Evidence |
|---|---:|---|---|---|
| 1 | 0:00–0:25 | Baseline/banner claim | `KLEIDIAI = 1` beside `0 compiled symbols` | `results/server/spark-provenance.txt` |
| 2 | 0:25–0:53 | L3 executed-kernel proof | Same binary and L1/L2 signals; L3 `0 → 7,968` with `--no-host` | `results/upstream/llamacpp-26334-cuda-host-buffer.json` |
| 3 | 0:53–1:22 | Measured cost | 7B prefill `48.64 → 222.14 tok/s`, `4.57x`; decode `1.65x` | `results/scale/scale-experiment.json` |
| 4 | 1:22–1:51 | Technical fixes/tooling | 9 s evidence card, then 20 s real `make demo` playback showing `MISMATCH` and `MATCH` outcomes | Spark provenance, Finding 4, `tools/polygraph`, live-capture receipt |
| 5 | 1:51–2:22 | Automated Arm64 readiness | Run `31294460364`: `1,543` requests, `0` failures, 10-minute c4 soak, 2 restarts, `10/10` checks | Arm64 summary + validation + workflow receipts |
| 6 | 2:22–2:49 | Honest boundary | `candidateOnly:true`, `productionReady:false`; Pro 6000 30B control labeled non-Arm | production-readiness portfolio |

## Beat 1 — baseline/banner claim

**On screen**

- `KLEIDIAI = 1`
- `0 compiled KleidiAI matmul symbols`
- explicit statement that this is a build finding, not an L3 result

**Narration intent**

Start with the software's own enabled banner, then immediately separate that
claim from static evidence. The documented Spark build had no kernel symbols,
so a responsible verifier must not pretend it observed execution.

## Beat 2 — L3 executed-kernel proof

**On screen**

- L1: 10 symbols in both arms
- L2: identical banner and I8MM selection log
- L3: baseline 0 hits; `--no-host` 7,968 hits
- 5/5 interleaved repetitions per arm

**Narration intent**

Make clear this is a *separate runtime failure* from Beat 1. It is the clean
proof of why L3 exists: static symbols and runtime selection text are identical,
but non-halting GDB breakpoints observe different execution.

Do not attach a performance number to this Finding 4 reproduction. Its evidence
explicitly says no timing sweep was run.

## Beat 3 — measured cost

**On screen**

- 7B prefill: `48.64 → 222.14 tok/s = 4.57x`
- 7B decode: `11.17 → 18.45 tok/s = 1.65x`
- five reps, round-robin interleaved, median

**Narration intent**

Return to Beat 1's zero-symbol build and show its measured cost. Keep the
boundary on the same card: one specific broken build, machine, compiler, and
toolchain — not a generic "KleidiAI is 4.57x" claim.

## Beat 4 — technical fixes and reusable tooling

**On screen**

- **First 9 seconds — evidence card**
  - build repair: `GGML_NATIVE=OFF` plus explicit Armv9.2/SVE2/I8MM flags;
  - runtime workaround for the separate CUDA host-buffer case: `--no-host` or
    `-dev none`;
  - `tools/polygraph check`: exit `0` match, `1` mismatch, `2` undetermined.
- **Next 20 seconds — real project-function footage**
  - actual local `make demo` PTY output;
  - liar case: identical `using fast path: yes` banner, L3 0 hits,
    `MISMATCH`, exit 1;
  - honest case: identical banner, L3 1 hit, `MATCH`, exit 0;
  - final harness checks confirm both outcomes are expected.

**Narration intent**

Do not collapse the two fixes. One restores compiled kernels; the other changes
runtime buffer selection. The reusable result is the verifier: L1, L2, and L3
remain separate, and uncertainty fails closed.

**Footage authenticity**

The renderer captures `make demo` from an isolated archive of the reviewed HEAD
with no inherited environment. Literal PTY output is unchanged. Line-reveal
pauses are normalized for legibility and the final captured frame is held so
both the liar `MISMATCH` and honest `MATCH` results can be read. The raw
transcript, clean transcript, timing events, GIF, receipt, and `SHA256SUMS` are
retained in `demo/contest-video/live-run/`.

## Beat 5 — automated Arm64 readiness

**On screen**

- GitHub Actions run `31294460364`;
- GitHub-hosted `aarch64`, four CPUs, Qwen2.5 1.5B Q4_0;
- 1,543 measured requests, zero failures;
- capacity c1/c2/c4 plus a 10-minute c4 soak;
- soak 55.45 output tok/s, TTFT p99 748.46 ms, E2E p99 1,924.57 ms;
- two clean restarts, slower readiness 1.5123 s;
- all 10 configured checks passed.

**Narration intent**

Show the transition from a diagnostic tool to an automated, judge-reproducible
Arm64 systems check. The run is production-shaped evidence, not a production
claim.

## Beat 6 — honest boundary

**On screen**

- `actualProductionTraffic:false`
- `productionReady:false`
- `candidateOnly:true`
- no multi-day availability, failover, multi-tenant isolation, or customer
  correctness evidence
- RTX PRO 6000 30B-A3B: **non-Arm systems evidence only**

**Narration intent**

End on the claim ceiling. The larger Pro 6000 control shows the serving/load
harness can exercise a 30B-class model, but it is x86_64/CUDA and cannot
validate Arm or KleidiAI dispatch.

## Production checks

```bash
python3 tools/render_contest_video.py --self-test
python3 tools/render_contest_video.py --dry-run
python3 tools/render_contest_video.py --capture-demo
python3 tools/render_contest_video.py \
  --validate-only demo/out/polygraph-contest-final.mp4
```

After the fresh capture has passed validation, `python3
tools/render_contest_video.py` may reuse it. The final sidecar regenerates the
authoritative duration/size/SHA block in `docs/VIDEO-PRODUCTION.md`.

Also run:

```bash
python3 tools/check_claims.py
python3 tools/lint_claims.py
```

No copyrighted music. The default render has narration only; `--tts none`
produces a silent AAC track and the same sidecar SRT.

Any prior 90-second or 103-second presenter/export cut is historical only. Do
not submit it as the current cut.
