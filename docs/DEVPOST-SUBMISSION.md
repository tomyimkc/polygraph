<!-- SPDX-License-Identifier: Apache-2.0 -->

# Arm Create Devpost submission — Polygraph

This is the judge-facing, paste-ready submission source. It deliberately uses the same structure
for every claim:

1. **Baseline** — what the software claimed or how the system behaved before the change.
2. **Technical change** — the exact verifier, build correction, patch, or evidence harness added.
3. **Measured impact** — only repository-backed observations, including regressions and null
   results.

Run `python3 tools/check_claims.py` before copying any prose. The checker fails if a scanned numeric
claim is not registered in `docs/CLAIMS.md` or backed by committed JSON.

## Submission identity

| Field | Paste-ready value |
|---|---|
| Project name | **Polygraph** |
| Tagline | **A fail-closed verification gate for Arm64 cloud inference: prove the advertised accelerated path ran before trusting the benchmark.** |
| Public repository | `https://github.com/tomyimkc/polygraph` |
| License | Apache-2.0 for this repository's original code, docs, tests, and evidence tooling; see `LICENSE`. Patch files against `llama.cpp` preserve that upstream project's MIT terms. |
| Interactive demo page | `https://tomyimkc-polygraph-arm-demo.static.hf.space/` |
| Dashboard | `https://tomyimkc.github.io/polygraph/` |
| Immutable evidence release | `https://github.com/tomyimkc/polygraph/releases/tag/arm-create-evidence-31312723300` |
| Primary upstream report | `https://github.com/ggml-org/llama.cpp/issues/26630` |
| Related upstream report | `https://github.com/ggml-org/llama.cpp/issues/26547` |
| Demo video | `https://youtu.be/er9PA5YYdzg` |
| Website media | `media/website-capture-20260813/01-demo-hero.png`, `02-demo-arm-evidence.png`, `03-demo-honest-rollback.png`, `04-demo-mobile.png` |
| Website media receipt | `media/website-capture-20260813/receipt.json` plus `media/website-capture-20260813/SHA256SUMS` |

The interactive HF demo page is the primary judge-facing walkthrough, and the YouTube link is the
submission video. The website media was
captured from that public static page, not from the repository's terminal video capture. The
receipt records HTTP 200, the page title, empty console/page error lists, the rendered headline
metrics, and SHA-256 hashes for all four PNG views. The historical repository MP4 remains
available for provenance, but is intentionally not used as the current submission media.

## Cloud AI fit and separation of claims

Polygraph is a **Cloud AI** project because it turns Arm64 inference-path verification into a
machine-readable CI and pre-deployment decision for `llama-server`. The measured workflow covers
the server binary, real kernel dispatch, concurrent throughput, time to first token, end-to-end
latency, peak RSS, sustained synthetic load, controlled restarts, and a keep-or-rollback gate.

The Arm CPU is not "lying." The potentially misleading claim comes from software: build feature
detection, a startup banner, runtime selection, or benchmark interpretation. Polygraph attributes
the mismatch to that layer instead of anthropomorphizing the chip.

The submission keeps three claims distinct:

| Evidence | Supported conclusion | Explicit non-claim |
|---|---|---|
| L1/L2/L3 | The measured workload entered the accelerated path | Not proof of speed, optimality, or readiness |
| Controlled benchmark | A tested build or candidate was faster or slower under that workload | Not a universal Arm or KleidiAI multiple |
| Readiness/promotion gate | The synthetic candidate met or missed configured checks | Not live traffic or deployment authorization |

The 4.57x headline is a **broken-versus-corrected build comparison** on one tested configuration.
Polygraph diagnosed and verified the correction; it did not invent a new matmul kernel. Stock
`llama.cpp` release binaries are not claimed to be affected. Patch `0002` is also not presented as
a promoted optimization: its isolated result did not survive the stricter Arm64 server gate, which
returned `ROLLBACK_TO_BASELINE`.

The relevant contest path is Arm CPU/KleidiAI inference. Although the DGX Spark host also has GPU
capability, the finding is on its Cortex-X925/A725 CPU, and the hosted Arm64 readiness workflow
explicitly starts `llama-server` with `-ngl 0`.

`make demo` is a portable fixture for the verifier and exit-code contract, not the Arm benchmark.
The Arm evidence is the committed build provenance, dispatch counts, server measurements, and
readiness/rollback receipts. See `docs/JUDGE-FAQ.md` for direct answers to likely judge objections.

## The claim ceiling judges should keep in view

The Arm64 readiness artifact records this exact boundary:

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

Run `31294460364` is a production-shaped, single-host, synthetic Arm64 evidence candidate. It is
not evidence of live customer traffic, multi-day availability, host or network failover,
multi-tenant isolation, or application-specific output correctness.

Long run `31312723300` is a same-binary, same-model temporal control. It completed 79,684 measured
requests with 0 measured failures across two shards. Its 36,000 measured seconds are **10
aggregate hours**, while the longest continuous segment is 9,000 seconds (**2.5 hours**). Both
shards returned `PASS` / `KEEP_CANDIDATE`, but this is not an optimization or uplift claim and
does not authorize deployment.

The RTX PRO 6000 run `31289517517` is a complementary larger-model systems control. Its artifact
records `actualProductionTraffic:false`, `armContestEvidence:false`, `candidateOnly:true`, and
`registeredResult:false`. It is **not** Arm contest evidence, is not a production-ready claim, and
must not be used for a cross-hardware performance ratio against the Arm64 run.

---

## Project overview

Polygraph answers a question a timer cannot: **did the accelerated code path the program advertised
actually execute?**

It checks three independent layers:

| Layer | Question | Mechanism | Claim ceiling |
|---|---|---|---|
| **L1 — static** | Is the accelerated kernel compiled into the binary? | `nm`, `otool`, or `objdump` symbol inspection | Presence only; not selection or execution |
| **L2 — selection** | What does the runtime log say it selected? | Parse the program's own verbose selection messages | Self-report only; still not execution |
| **L3 — dispatch** | Did the kernel's machine code run? | Non-halting `lldb`/`gdb` breakpoints and per-symbol hit counts | Direct execution evidence for the measured workload |

`tools/polygraph` packages that method as a stdlib-only CLI with built-in targets and an ad-hoc
mode for any binary, symbol regex, and workload command. Its exit-code contract is fail-closed:

- `0` — advertised capability matches what executed;
- `1` — mismatch;
- `2` — undetermined because the evidence could not be collected; never silently reported as a
  match.

---

## Judge-first proof chains

### Proof chain 1 — the documented KleidiAI build can advertise acceleration with zero usable matmul kernels

#### Baseline

On a DGX Spark with a Cortex-X925 and gcc 13.3, `llama.cpp`'s documented
`-DGGML_CPU_KLEIDIAI=ON` build completed successfully and printed `KLEIDIAI = 1`, but the built
library contained **0** `kai_run_matmul` entry points. On the measured 7B model, the broken build
delivered 48.64 tok/s prefill and 11.17 tok/s decode.

#### Technical change

The build was repeated from the same source, commit, compiler, and machine with the explicit
feature-target pair:

```text
-DGGML_NATIVE=OFF
-DGGML_CPU_ARM_ARCH=armv9.2-a+sve2+i8mm+bf16+dotprod
```

That correction restored all 10 measured `kai_run_matmul` entry points and the expected I8MM
selection messages. Polygraph separates the misleading banner from the static and runtime
evidence instead of treating `KLEIDIAI = 1` as proof.

#### Measured impact

On the same 7B model, the fixed build delivered **222.14 tok/s prefill and 18.45 tok/s decode**:
**4.57x prefill** and **1.65x decode** versus the broken build.

The negative result stays attached to the headline: on the 0.5B model, prefill improved by 1.42x
but decode was **0.99x — no measurable effect**. This is a specific broken-build comparison on one
machine and toolchain, not a general claim that KleidiAI is always 4.57x faster and not headroom
available to stock `llama.cpp` users.

**Evidence:** `results/server/spark-provenance.txt`,
`results/scale/scale-experiment.json`, `docs/UPSTREAM-ISSUE-FINDING3.md`.

### Proof chain 2 — an isolated optimization result failed the stronger cloud promotion gate

#### Baseline

On the measured Apple M4 Max, unmodified `llama.cpp` with no thread flags used 12 generation
threads and 12 batch threads. The round-robin-interleaved `llama-cli` measurement recorded
**67.8 tok/s decode** and **1,835.2 tok/s prefill**.

The obvious manual workaround, `-t 2`, reached the decode ceiling but also made batch threads
inherit 2, reducing prefill to 975.6 tok/s — a **47% regression**.

#### Technical change

`patches/0002-kleidiai-sme-aware-thread-default.patch` reads KleidiAI's runtime-detected SME2
thread cap and, only when the user did not set a generation thread count, defaults generation to
that cap while leaving batch/prefill threads at the normal default. It preserves explicit user
choices and includes `GGML_KLEIDIAI_AUTO_THREADS=0` as a kill switch.

#### Measured impact

With no flags, the patched binary recorded **145.9 tok/s decode**, a **2.15x** increase over the
67.8 tok/s baseline and statistically indistinguishable from the hand-tuned ceiling. Prefill was
1,779.8 tok/s, **-3.0% and within the measured noise band**, rather than the 47% regression caused
by the naive one-flag workaround.

The stronger follow-up is deliberately unflattering. In a three-round distinct-binary
`llama-server` campaign, the harness omitted `-t`/`-tb`, alternated AB/BA order, replayed identical
synthetic traces, retained controlled-restart evidence, and required median paired throughput of
at least `1.0x`. Patch `0002` produced `0.9330x` throughput, `1.2464x` E2E p99, and `1.7081x`
TTFT p99, winning throughput in only one of three rounds. The strict verdict was
`FAIL / ROLLBACK_TO_BASELINE`.

The claim is therefore two-part: the patch is a real implementation that produced the recorded
isolated win, but the fixed SME-cap heuristic is not robust enough to promote as a production
server default. The measured 1.5B sweep also found that the cap-selected thread count missed the
best measured decode point by about 17.5%.

**Evidence:** `patches/0002-kleidiai-sme-aware-thread-default.patch`,
`results/AUTODEFAULTS.md`, `results/GENERALIZATION.md`,
`results/production-readiness/arm64-0.5b-autodefault-differential-confirmation-20260811/`.

### Proof chain 3 — L1 and L2 can both agree while execution still silently falls back

#### Baseline

With one `llama.cpp` binary built with both CUDA and KleidiAI, CPU-only `-ngl 0` inference showed
the same `KLEIDIAI = 1` banner, the same I8MM selection line, and the same 10/149 symbol count as
the working arms. L3 recorded **0 KleidiAI dispatch hits** in every one of five interleaved
repetitions, and Polygraph exited `1`.

#### Technical change

The same binary and model were run with either `--no-host` or `-dev none`, removing the
CUDA-host-buffer priority condition that bypassed the KleidiAI buffer path.

#### Measured impact

Both corrected arms recorded **7,968 dispatch hits** in every one of five repetitions and
Polygraph exited `0`. This finding makes **no performance claim**; it proves only the advertised
versus executed mismatch and the restoration of execution.

The mechanism was originally reported by `izard` in `llama.cpp` issue `#26334`. Polygraph does
not claim discovery; its contribution is the 15-run debugger-backed reproduction and exact
execution counts.

**Evidence:** `results/upstream/llamacpp-26334-cuda-host-buffer.json`,
`results/upstream/FINDING-4-CUDA-HOST-BUFFER.md`.

### Proof chain 4 — contest evidence now extends from microbenchmarks to a production-shaped Arm64 server run

#### Baseline

The earlier Arm evidence established symbols, selection, dispatch, correctness, and throughput,
but did not test sustained mixed server traffic, one-second telemetry, or controlled recovery.

#### Technical change

`tools/server_readiness.py` now:

- starts a pinned CPU-only `llama-server`;
- drives short-chat, RAG-like, and longer-summary traffic;
- measures capacity at concurrency 1, 2, and 4;
- runs a 10-minute concurrency-4 soak;
- retains one row per request and one-second telemetry;
- performs two controlled stop/start cycles with canary requests; and
- evaluates 10 explicit architecture, request, correctness-of-accounting, latency, recovery, and
  RSS checks.

The manual `server-readiness` job in `.github/workflows/verify-free-arm64.yml` executes this on
GitHub's hosted `ubuntu-24.04-arm` runner.

#### Measured impact

Arm64 workflow run **`31294460364`** completed **1,543 measured requests with 0 failures**. The
10-minute sustained phase recorded **55.45 output tok/s**, **748.46 ms TTFT p99**, and
**1,924.57 ms end-to-end p99**. Maximum server RSS was **1,869.69 MiB**; the slower restart
readiness measurement was **1.5123 seconds**. All 10 configured checks passed.

This is an evidence-harness result, not a promotion to production readiness. The authoritative
artifact still says:

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

**Evidence:** `results/production-readiness/arm64-31294460364/summary.json`,
`validation-receipt.json`, `workflow-receipt.json`, request JSONL, telemetry, server log, checksum
manifests, and `contest-matrix/`.

### Proof chain 5 — a long same-artifact Arm64 campaign tests the evidence machinery without inventing uplift

#### Baseline

The first Arm64 readiness run preserves a 10-minute soak and controlled restarts, but not a longer
paired temporal-control exposure. Baseline/candidate labels also create a claim risk unless the
artifact identity and aggregate-duration semantics are explicit.

#### Technical change

The main-only `.github/workflows/verify-production-arm64.yml` campaign:

- pins the exact Polygraph source, reviewed `llama.cpp` commit, model file, and model hash;
- reuses the same `llama-server` and model bytes for baseline and temporal-candidate labels;
- generates deterministic shard-scoped synthetic traces rather than production traffic or PII;
- exercises capacity, readiness, replay, controlled process restart/model reload, deterministic
  load-generator delay/error, and rollback-decision checks;
- runs two independent hosted Arm64 shards; and
- retains strict shard receipts, build provenance, original GitHub ZIP digests, workflow metadata,
  and an independently recomputed aggregate.

#### Measured impact

Smoke preflight `31312308726` passed before the long dispatch. Long run `31312723300` then
completed **79,684 measured requests with 0 measured failures**. Both shards returned
`PASS` / `KEEP_CANDIDATE`, used identical baseline/candidate trace digests, and recorded
`sameArtifactControl:true`.

The duration is **36,000 aggregate measured seconds (10 aggregate hours)** across four independent
segments on two parallel shards. The longest continuous segment is **9,000 seconds (2.5 hours)**.
It is not 10 continuous hours. Because the artifacts are identical, no optimization or uplift is
claimed.

**Evidence:**
`results/production-readiness/arm64-campaign-31312308726-31312723300/`,
especially `long-validation-receipt.json`, `long-aggregate-receipt.json`, both strict shard
receipts, workflow metadata, and `package-sha256sums.txt`; original GitHub ZIPs and build
provenance are in release `arm-create-evidence-31312723300`.

### Proof chain 6 — a larger-model non-Arm control tests the workload method without inflating the Arm claim

#### Baseline

The Arm64 readiness run is a single-host Qwen2.5 1.5B Q4_0 campaign. That is contest-relevant but
too narrow to imply that the production-shaped workload and artifact discipline were only usable
for one small model or one runtime.

#### Technical change

The same evidence philosophy was applied separately on an owned x86_64/CUDA RTX PRO 6000 using
vLLM and immutable `Qwen/Qwen3-30B-A3B-Instruct-2507` model revision
`0d7cf23991f47feeb3a57ecb4c9cee8ea4a17bfe`: mixed traffic, concurrency 1/4/8/16 capacity
points, a 15-minute concurrency-8 soak, one-second host/GPU telemetry, request-level JSONL, and
checksums.

#### Measured impact

Control run **`31289517517`** retained **7,573 measured requests with 0 failures**, plus 1,071
successful warm-up requests, with usage accounting present for every measured request.

That result is deliberately quarantined from the Arm score:

```text
actualProductionTraffic:false
armContestEvidence:false
candidateOnly:true
registeredResult:false
```

The control aggregate does not contain a `productionReady` field; this submission does not infer
one. It is a larger-model systems control, not Arm evidence, not a production-ready claim, and not
a basis for an Arm-versus-GPU ratio.

**Evidence:** `results/production-readiness/pro6000-31289517517/aggregate.json`,
`README.md`, capacity and soak summaries, request JSONL, telemetry, preflight receipts,
`workflow-receipt.json`, and `sha256sums.txt`.

### Proof chain 7 — the verifier is applied to this project's own claims

#### Baseline

An early result published a **retracted +57.3%** patch win after comparing baseline and patched
configurations measured in unevenly contended time windows. A separate first patch,
`0001-kleidiai-phase-aware-dispatch.patch`, changed dispatch as designed but measured about
**12% slower** at the default thread count.

#### Technical change

The project:

- published the correction rather than deleting it;
- re-measured in round-robin-interleaved order;
- created `docs/CLAIMS.md` plus `tools/check_claims.py`;
- added unit tests for the claims gate, verifier, CLI, target schema, and MCP server; and
- retained negative and non-generalizing outcomes next to the positive claims.

#### Measured impact

The project no longer promotes patch `0002` from its isolated local result. Its stronger
distinct-binary Arm64 `llama-server` campaign returned `ROLLBACK_TO_BASELINE`, so it remains an
experimental candidate and evidence for why the promotion gate exists. Patch `0001` remains a
negative result. The original 0.5B tuning result was also re-tested at 7B and shrank from 4.56x to
1.33x rather than being presented as universal.

**Evidence:** `results/REMEASURE-2026-08-04-QUIET.md`, `results/AUTODEFAULTS.md`,
`results/scale/scale-experiment.json`, `docs/CLAIMS.md`, `CHANGELOG.md`.

---

## Judging criteria mapping

### Technological Implementation — 40 points

**Baseline:** banners, logs, and timers can all look healthy during a silent fallback.

**Technical change:** Polygraph combines L1 symbol evidence, L2 selection evidence, and L3
debugger dispatch counts; packages them behind a fail-closed CLI; validates the L3 probe against a
ground-truth harness; adds cross-platform `lldb`/`gdb`; exposes the method through a dependency-free
MCP server; and pins numeric prose through a claims registry.

**Measured impact:** the method separated 0 from 7,968 real kernel calls when L1 and L2 were
identical, found a zero-kernel build behind `KLEIDIAI = 1`, verified dispatch under concurrent
serving load, and produced a fully retained Arm64 capacity/soak/restart artifact.

**Judge evidence:** `tools/polygraph`, `tools/verify_dispatch.py`, `tools/server_readiness.py`,
`tests/`, `.github/workflows/verify-free-arm64.yml`,
`results/production-readiness/arm64-31294460364/`.

### "WOW" factor — 25 points

**Baseline:** the documented build exits successfully and advertises KleidiAI.

**Technical change:** count what compiled and what executed instead of trusting the banner.

**Measured impact:** 0 usable matmul entry points were present in the broken build; the corrected
build restored 10, and the measured 7B prefill comparison was 48.64 to 222.14 tok/s, or 4.57x.
The second reveal is epistemic: the project also published its own retraction, regression, and
shrinking generalization result.

**Judge evidence:** `results/server/spark-provenance.txt`,
`results/scale/scale-experiment.json`, `results/REMEASURE-2026-08-04-QUIET.md`.

### Potential Impact — 20 points

**Baseline:** maintainers and release engineers can ship an advertised-versus-executed mismatch
without a crash, failed build, or obvious latency anomaly.

**Technical change:** provide a generic binary/symbol/workload verifier, a one-command CI contract,
public upstream reports, reusable target presets, and evidence artifacts that preserve raw rows and
claim boundaries.

**Measured impact:** two upstream reports covering the project's original findings were filed; the
independent `#26334` mechanism was reproduced with 15 debugger-backed runs; and the public
Apache-2.0 repository gives other projects a concrete pattern for checking their own accelerated
paths.

**Scope limit:** Finding 3 does not affect stock `llama.cpp` releases. The exposed population is
source builders and release pipelines that explicitly enable KleidiAI under the measured
compiler/CPU condition.

### User Experience / Developer Experience — 15 points

**Baseline:** proving real dispatch normally requires custom debugger scripting and specialist
knowledge.

**Technical change:** ship one command, built-in presets, ad-hoc mode, JSON output, `--quiet`,
`--level`, explicit exit codes, a no-model catch-a-liar demo, and a free GitHub-hosted Arm64
workflow.

**Measured impact:** a judge can verify both detector directions in about two minutes without Arm
hardware or a model download:

```bash
git clone https://github.com/tomyimkc/polygraph.git
cd polygraph
make demo
```

The liar reports a mismatch and the honest build reports a match. If the debugger is unavailable,
the tool exits `2` as undetermined instead of claiming success.

---

## Reproduction commands

### Fastest judge path: prove the detector works

```bash
git clone https://github.com/tomyimkc/polygraph.git
cd polygraph
make demo
python3 tools/check_claims.py
```

### Inspect and verify Arm64 run `31294460364`

```bash
cd results/production-readiness/arm64-31294460364
sha256sum -c sha256sums.txt
wc -l measured-requests.jsonl warmup-requests.jsonl
jq '{
  actualProductionTraffic,
  productionReady,
  candidateOnly,
  totals,
  soak: .results[] | select(.phase == "soak"),
  gate
}' summary.json
jq '{runId, headSha, conclusion, url, jobs, artifacts}' workflow-receipt.json
jq '{headSha, checks, contestMatrix}' validation-receipt.json
```

Expected high-level result: 1,543 measured request rows, 11 warm-up rows, gate `PASS`, and the exact
`false/false/true` claim boundary.

### Inspect and verify long Arm64 campaign `31312723300`

```bash
cd results/production-readiness/arm64-campaign-31312308726-31312723300
sha256sum -c package-sha256sums.txt
jq '{source, checks, durations, totals, shards, boundary}' long-validation-receipt.json
jq '{
  actualProductionTraffic,
  productionReady,
  candidateOnly,
  deploymentAuthorized,
  tenantIsolationClaimed,
  aggregateMeasuredSoakSeconds,
  longestContinuousSoakSegmentSeconds,
  shards
}' long-aggregate-receipt.json
```

Expected high-level result: 79,684 measured requests, 0 measured failures, exact artifact/provenance
checks true, 36,000 aggregate measured seconds, 9,000-second longest continuous segment, and both
shards `PASS` / `KEEP_CANDIDATE`.

To rerun the same long profile on a public fork's `main` branch:

```bash
gh workflow run verify-production-arm64.yml \
  --repo OWNER/polygraph \
  --ref main \
  -f campaign_mode=long \
  -f model_id=qwen2.5-1.5b-q4_0 \
  -f matrix_shards=2 \
  -f repetitions=0 \
  -f soak_seconds_total=0
```

### Re-run the free hosted Arm64 workflow

On a public fork with GitHub CLI authentication:

```bash
gh workflow run verify-free-arm64.yml --repo OWNER/polygraph
gh run list --repo OWNER/polygraph --workflow verify-free-arm64.yml --limit 1
gh run watch --repo OWNER/polygraph RUN_ID --exit-status
```

The longer `server-readiness` job runs only on `workflow_dispatch`. The workflow itself pins the
model, source revision, build command, harness arguments, evidence-boundary assertions, and
artifact upload.

### Run the readiness harness locally after building the pinned server and model

```bash
python3 tools/server_readiness.py \
  --server "$LLAMA_SERVER" \
  --model "$MODEL_PATH" \
  --out-dir "$READINESS_OUT" \
  --threads "$(nproc)" \
  --threads-batch "$(nproc)" \
  --server-parallel 4 \
  --capacity-concurrencies 1,2,4 \
  --capacity-seconds 30 \
  --soak-concurrency 4 \
  --soak-seconds 600 \
  --warmup-requests-per-worker 1 \
  --restart-cycles 2 \
  --max-tokens 24 \
  --request-timeout 60 \
  --ready-timeout 120 \
  --min-measured-requests 50 \
  --max-error-rate 0 \
  --max-ttft-p99-ms 5000 \
  --max-e2e-p99-ms 30000 \
  --max-recovery-seconds 120 \
  --max-rss-mib 8192
```

### Reproduce the zero-kernel build correction

Run against `llama.cpp` commit `dbadb68` on the measured class of aarch64 Linux system:

```bash
cmake -S . -B build-broken \
  -DGGML_CPU_KLEIDIAI=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-broken -j

cmake -S . -B build-fixed \
  -DGGML_CPU_KLEIDIAI=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DGGML_CPU_ARM_ARCH="armv9.2-a+sve2+i8mm+bf16+dotprod"
cmake --build build-fixed -j

nm -D build-broken/bin/libggml-cpu.so |
  grep -c '^[0-9a-f]* T kai_run_matmul'
nm -D build-fixed/bin/libggml-cpu.so |
  grep -c '^[0-9a-f]* T kai_run_matmul'
```

### Reproduce the CUDA-host-buffer dispatch mismatch

The three dedicated presets encode the baseline, `--no-host`, and `-dev none` workloads. Run from
a checkout where `BINARY` points to the CUDA+KleidiAI `llama-cli` build and `MODEL` points to the
measured `q05.gguf` model:

```bash
cmake -S . -B build-cuda-kleidiai -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CPU_KLEIDIAI=ON -DGGML_CUDA=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_CPU_ARM_ARCH="armv9.2-a+sve2+i8mm+bf16+dotprod"
cmake --build build-cuda-kleidiai -j

BINARY="${BINARY:-build-cuda-kleidiai/bin/llama-cli}"
MODEL="${MODEL:-q05.gguf}"

set +e
python3 tools/polygraph check llama-cpp-kleidiai-cuda-ngl0-baseline \
  --binary "$BINARY" --model "$MODEL" \
  --l3-timeout 60 --l2-timeout 30
baseline_rc=$?

python3 tools/polygraph check llama-cpp-kleidiai-cuda-ngl0-nohost \
  --binary "$BINARY" --model "$MODEL" \
  --l3-timeout 60 --l2-timeout 30
nohost_rc=$?

python3 tools/polygraph check llama-cpp-kleidiai-cuda-ngl0-devnone \
  --binary "$BINARY" --model "$MODEL" \
  --l3-timeout 60 --l2-timeout 30
devnone_rc=$?
set -e

printf 'baseline=%s nohost=%s devnone=%s\n' \
  "$baseline_rc" "$nohost_rc" "$devnone_rc"
test "$baseline_rc" -eq 1
test "$nohost_rc" -eq 0
test "$devnone_rc" -eq 0
```

### Inspect and verify non-Arm control `31289517517`

```bash
cd results/production-readiness/pro6000-31289517517
sha256sum -c sha256sums.txt
wc -l capacity-sweep/requests-c*.jsonl soak-c8/requests-c8.jsonl
jq '{
  runId,
  gitSha,
  model,
  modelRevision,
  totals,
  actualProductionTraffic,
  armContestEvidence,
  candidateOnly,
  registeredResult
}' aggregate.json
cat workflow-receipt.json
```

Do not turn this control into Arm evidence or compare its throughput numerically with the Arm64
run: the hardware, model, runtime, output lengths, and concurrency plans differ.

---

## Negative results and limitations — keep this visible

- The earlier **+57.3%** patch win was retracted after the measurement-order flaw was found.
- `patches/0001-kleidiai-phase-aware-dispatch.patch` changed dispatch as intended but measured about
  **12% slower** at the default thread count; it is not promoted as an optimization.
- The 0.5B instance of Finding 3 produced **0.99x decode**, a null result.
- The original tuning multiple shrank from 4.56x at 0.5B to 1.33x at 7B.
- Patch `0002`'s mechanism works in the tested grid, but its SME-cap thread heuristic missed the
  measured 1.5B optimum by about 17.5%.
- Finding 3 is one gcc 13.3/Cortex-X925 build condition and does not affect stock releases.
- The Arm64 readiness gate is synthetic and single-host. The supplementary long campaign has 10
  aggregate measured hours, but the longest continuous segment is 2.5 hours; neither is a
  production-readiness or application-quality claim.
- The long baseline/candidate labels use identical server and model artifacts, so their temporal
  differences are not an optimization or uplift result.
- The PRO 6000 control is x86_64/CUDA and cannot support an Arm contest claim.
- The automated Spark workflow remains best-effort; the committed SVE2 dispatch confirmation came
  from a separate manual debugger measurement.
- The project claims filed evidence, not maintainer acceptance, agreement, or an upstream fix.

---

## Historical video provenance (not current submission media)

The repository-native final cut remains available for reproducibility and historical provenance:

```text
demo/out/polygraph-contest-final.mp4
```

Its validation receipt records **169.021333 seconds (2:49.021)**, **9,239,731 bytes**, and
SHA-256 `0deb7f67697b45ac8b9b38b518d87240ac556db8bb82107f23158862372f8f30`. The authored
captions, validation receipt, and checksum are retained beside it. These artifacts document the
earlier terminal-capture workflow only; they are not the current contest media and do not replace
the website capture set under `media/website-capture-20260813/`.

For provenance, the rendered story used this six-beat sequence:

| Time | Judge question | Baseline → technical change → measured impact |
|---|---|---|
| 0:00–0:25 | Why distrust the banner? | `llama.cpp`'s documented KleidiAI build prints `KLEIDIAI = 1` → inspect symbols → find 0 usable matmul entry points |
| 0:25–0:53 | Why is L3 necessary? | L1/L2 agree in the CUDA-host-buffer case → count non-halting debugger hits → 0 versus 7,968 |
| 0:53–1:22 | What did the build defect cost? | Broken 7B build → explicit feature-target repair → 48.64 to 222.14 tok/s prefill, 4.57x |
| 1:22–1:51 | What changed technically? | Separate build and runtime failures → separate build flags/workarounds plus reusable L1/L2/L3 CLI → explicit exit codes |
| 1:51–2:22 | Is there Arm server evidence? | Prior microbenchmarks only → automated hosted Arm64 readiness harness → run `31294460364`, 1,543 requests, 0 failures |
| 2:22–2:49 | Does the result overclaim? | Synthetic PASS could be misread → show exact boundary → `actualProductionTraffic:false`, `productionReady:false`, `candidateOnly:true`; PRO 6000 remains non-Arm |

The historical video was finalized before long run `31312723300` and correctly shows the earlier
Arm64 readiness run `31294460364`. The long campaign is a post-video evidence addendum in the
repository and immutable release; do not claim that the historical video depicts it.

The historical cut includes 20 seconds of the real, isolated `make demo` capture. Its capture receipt
records a strict allowlisted environment, bounded process group and outputs, the exact capture
driver and source archive hashes, transcript reconstruction, and the expected liar/honest exit
contract. The encoded playback preserves captured text and ordering while normalizing pauses for
legibility. Current judge-facing media is captured from the public HF demo page instead.

---

## Built with

Arm KleidiAI, Arm SME2, Arm SVE2, Arm NEON/I8MM/DOTPROD, `llama.cpp`, C, C++, Python, Bash,
CMake, `lldb`, `gdb`, GitHub Actions, vLLM for the non-Arm control, JSONL, MCP, and Apache-2.0
project licensing.

---

# Final paste-ready Devpost block

## Project Overview

**Track: Cloud AI.** Polygraph is a fail-closed verification and deployment gate for Arm64 cloud
inference. Instead of trusting a startup banner or timing result, it checks three separate layers:
whether accelerated kernels exist in the server binary, what the runtime says it selected, and
whether those kernels actually executed under a non-halting debugger breakpoint. It then measures
throughput, time to first token, end-to-end latency, memory, and recovery separately before a
candidate is kept or rolled back.

The Arm CPU is not "lying." The potentially misleading signal comes from the software build,
feature probe, startup report, dispatcher, or benchmark interpretation. L1/L2/L3 proves execution
provenance for the measured workload; it does not by itself prove speed, optimality, application
correctness, or production readiness.

The headline baseline was a `llama.cpp` build on a DGX Spark that completed successfully and
printed `KLEIDIAI = 1`, yet contained 0 usable `kai_run_matmul` entry points. The technical change
was an explicit Arm feature-target build plus a reusable L1/L2/L3 verification tool. The measured
impact on the tested 7B model was 48.64 to 222.14 tok/s prefill, a 4.57x comparison, and 11.17 to
18.45 tok/s decode, a 1.65x comparison. The 0.5B decode result was 0.99x, so we report it as no
measurable effect rather than generalizing the 7B result. This is a broken-versus-corrected
source-build comparison on one Arm CPU configuration. Polygraph did not invent a new matmul
kernel, and stock `llama.cpp` releases are not claimed to contain this defect.

We also built an experimental optimization candidate: patch `0002` changes only the default
generation thread count to KleidiAI's runtime-detected SME2 cap while preserving the
batch/prefill default. An isolated Apple M4 Max run was favorable, but the stronger test was the
distinct-binary Arm64 `llama-server` campaign. Its median candidate/baseline throughput was
0.9330x and its verdict was `FAIL / ROLLBACK_TO_BASELINE`. We therefore do not present the patch
as a promoted optimization.

The repository is public and Apache-2.0 licensed. Its measurement artifacts, raw request rows,
telemetry, workflow receipts, validation receipts, negative results, and claim checker are all
committed for judges to inspect.

**Challenge-period confirmation:** all work submitted here was created or meaningfully updated
during the challenge period. The public commit history, timestamped workflow receipts, and
immutable evidence release provide the provenance record.

The final evidence package also adds a same-artifact Arm64 temporal-control campaign: 79,684
measured requests, 0 measured failures, 36,000 aggregate measured seconds across two shards, and a
9,000-second longest continuous segment. Both shards passed the synthetic gate and rollback
decision. Because the baseline and candidate artifacts are identical, this is evidence about the
campaign machinery and longer aggregate exposure — not an optimization or uplift claim.

## Functionality / Output

The primary interface is:

```bash
tools/polygraph list
tools/polygraph explain TARGET
tools/polygraph check TARGET
tools/polygraph check --binary PATH --symbols REGEX --run "COMMAND"
```

Polygraph returns human-readable and JSON output plus contractual exit codes: `0` for a match, `1`
for a measured mismatch, and `2` for undetermined. The two-minute `make demo` compiles two tiny
programs that print the same `using fast path: yes` banner. L3 then proves that the liar never
calls the fast function and the honest build does. This portable fixture demonstrates the
verifier and exit-code contract; it is not the Arm benchmark.

The current Arm64 evidence is GitHub Actions run `31294460364`. It adds a production-shaped
CPU-only `llama-server` campaign (`-ngl 0`): mixed traffic, concurrency 1/2/4 capacity points, a 10-minute
concurrency-4 soak, request-level JSONL, one-second telemetry, and two controlled restarts. It
completed 1,543 measured requests with 0 failures; all 10 configured checks passed. Its exact
claim boundary is still:

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

The complementary RTX PRO 6000 run `31289517517` retained 7,573 measured requests with 0 failures
on a larger model, but it is explicitly `armContestEvidence:false` and is included only as a
non-Arm systems control.

The supplementary Arm64 run `31312723300` used the exact same server/model artifacts for baseline
and temporal-candidate labels. It retained 79,684 measured requests with 0 measured failures.
Its 36,000 seconds are 10 aggregate measured hours across four independent segments on two shards;
the longest continuous segment is 9,000 seconds (2.5 hours). Both shards returned `PASS` /
`KEEP_CANDIDATE`, with `productionReady:false` and `deploymentAuthorized:false`.

## Setup Instructions

Fastest path, with no Arm hardware and no model download:

```bash
git clone https://github.com/tomyimkc/polygraph.git
cd polygraph
make demo
python3 tools/check_claims.py
```

To reproduce the contest's free Arm64 pipeline, fork the public repo and run
`verify-free-arm64.yml` from GitHub Actions. The workflow uses GitHub's hosted
`ubuntu-24.04-arm` runner, pins the `llama.cpp` revision and model hashes, runs the three-model
dispatch/throughput matrix, and on manual dispatch runs the longer server-readiness job.

The separate `verify-production-arm64.yml` manual workflow reproduces the same-artifact smoke/long
campaign and is restricted to the fork's `main` branch. The exact long dispatch fields and
receipt-verification commands are in `docs/CONTEST-EVIDENCE-MAP.md`.

Exact local commands, checksum checks, workflow commands, and artifact inspection commands are in
`docs/CONTEST-EVIDENCE-MAP.md`.

## Why it should win

**Technological Implementation — 40 points:** Polygraph does not stop at a benchmark. It combines
static symbols, runtime selection, and real execution counts; works across `lldb` and `gdb`; has a
fail-closed CLI and CI contract; validates its own debugger probe; exposes an MCP interface; and
pins every numeric prose claim to evidence. For Cloud AI it connects that provenance to CPU-only
`llama-server` concurrency, latency, memory, restart, and rollback checks.

**"WOW" factor — 25 points:** a successful build said `KLEIDIAI = 1` while shipping 0 usable
matmul entry points. Correcting that tested source build produced a 4.57x measured 7B prefill
comparison. This is explicitly not a new kernel or universal Arm speedup. In a second finding, the
banner, selection log, and symbol count were identical while L3 changed from 0 to 7,968 actual
kernel calls.

**Potential Impact — 20 points:** the tool is generic to any binary and symbol regex, the repo is
public under Apache-2.0, two upstream reports cover the project's original findings, and an
independently reported mechanism was reproduced with 15 debugger-backed runs. The impact statement
stays honest: the zero-kernel defect does not affect stock releases. The target users are source
builders, framework/release engineers, CI owners, and cloud inference operators who need to stop a
silent fallback or invalid performance assumption before fleet rollout.

**User Experience / Developer Experience — 15 points:** one clone and `make demo` proves both the
positive and negative detector paths in about two minutes. JSON output, presets, ad-hoc mode,
explicit exit codes, graceful degradation, and a free hosted Arm64 workflow make the method usable
without specialized hardware.

Most importantly, Polygraph applies its standard to itself. We published a retraction, kept a
slower patch visible, recorded a no-effect result, and refused to turn a synthetic readiness PASS
or a non-Arm control into a production or Arm claim.

## Judge evidence index

1. **Start here — interactive demo page:** `https://tomyimkc-polygraph-arm-demo.static.hf.space/`.
2. **Website media captures:** `media/website-capture-20260813/` and its receipt/checksum manifest.
3. **Reproducible static-page source bundle:** `space/` (the exact website-only bundle used by the
   public HF Space, with no terminal video asset).
4. **Claim-by-claim evidence:** `docs/CONTEST-EVIDENCE-MAP.md`.
5. **Scope and objections:** `docs/JUDGE-FAQ.md`.
6. **Run the product:** `make demo`, then inspect `tools/polygraph` and `docs/QUICKSTART.md`.
7. **Headline build finding:** `results/server/spark-provenance.txt` and
   `results/scale/scale-experiment.json`.
8. **Experimental candidate and rollback:** `patches/0002-kleidiai-sme-aware-thread-default.patch`,
   `results/AUTODEFAULTS.md`, and the Arm64 differential `receipt.json`.
9. **L1/L2 agree but L3 fails:** `results/upstream/FINDING-4-CUDA-HOST-BUFFER.md` and its
   15-run JSON.
10. **Arm64 run `31294460364`:**
   `results/production-readiness/arm64-31294460364/summary.json`,
   `validation-receipt.json`, and `workflow-receipt.json`.
11. **Long same-artifact Arm64 campaign `31312723300`:**
   `results/production-readiness/arm64-campaign-31312308726-31312723300/long-validation-receipt.json`,
   aggregate/shard receipts, and `package-sha256sums.txt`.
12. **Immutable evidence release:** `arm-create-evidence-31312723300`, preserving original GitHub
   artifact ZIPs, build provenance, campaign source, external validations, and historical video
   provenance.
13. **Non-Arm control `31289517517`:**
   `results/production-readiness/pro6000-31289517517/aggregate.json` and `README.md`.
14. **Negative results and corrections:** `results/REMEASURE-2026-08-04-QUIET.md`,
   `patches/README.md`, `results/GENERALIZATION.md`.
15. **Claim integrity:** `docs/CLAIMS.md`, `tools/check_claims.py`, and
   `.github/workflows/claims.yml`.
16. **Final submission check:** `docs/CONTEST-SUBMISSION-CHECKLIST.md`.
