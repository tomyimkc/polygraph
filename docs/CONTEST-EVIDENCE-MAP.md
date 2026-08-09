<!-- SPDX-License-Identifier: Apache-2.0 -->

# Arm Create contest evidence map

This file is the judge's shortest route from each submission statement to a committed artifact.
Every technical claim is written as **Baseline → Technical change → Measured impact**. Negative
results and claim boundaries are first-class evidence, not footnotes.

## Claim boundary

The Arm64 readiness candidate carries the exact fields:

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

Interpretation:

- the configured synthetic gate passed;
- the artifact is suitable as Arm contest evidence;
- it is not live customer traffic;
- it is not a production-readiness verdict; and
- it is not proof of multi-day availability, failover, tenant isolation, or application
  correctness.

The longer same-artifact campaign keeps the same three fields. Its aggregate additionally
records `deploymentAuthorized:false` and `tenantIsolationClaimed:false`; the external
validation receipt records `durations.continuousAvailabilityClaimed:false`. Its 36,000 measured
seconds are **10 aggregate hours** across four independent segments; the longest continuous
segment is 9,000 seconds, or **2.5 hours**.

The RTX PRO 6000 control records:

```text
actualProductionTraffic:false
armContestEvidence:false
candidateOnly:true
registeredResult:false
```

Its aggregate has no `productionReady` field. Do not infer one. It is a non-Arm systems control,
not Arm contest evidence and not a cross-hardware benchmark baseline.

## Five-minute judge route

1. Run `make demo`.
2. Open `results/server/spark-provenance.txt`.
3. Open `results/scale/scale-experiment.json`.
4. Open `results/production-readiness/arm64-31294460364/summary.json`.
5. Open the adjacent `validation-receipt.json` and `workflow-receipt.json`.
6. Open
   `results/production-readiness/arm64-campaign-31312308726-31312723300/long-validation-receipt.json`.
7. Run `python3 tools/check_claims.py`.
8. Read the negative-result index near the end of this file.

## Proof-chain map

### E1 — documented build advertises KleidiAI but compiles zero usable matmul entry points

**Baseline**

- Build: `-DGGML_CPU_KLEIDIAI=ON`, Release, `llama.cpp` commit `dbadb68`.
- Host: DGX Spark, aarch64 Cortex-X925, gcc 13.3.
- Banner: `KLEIDIAI = 1`.
- Static result: 0 `kai_run_matmul` symbols.
- Measured 7B result: 48.64 tok/s prefill and 11.17 tok/s decode.

**Technical change**

- Add `-DGGML_NATIVE=OFF`.
- Add
  `-DGGML_CPU_ARM_ARCH="armv9.2-a+sve2+i8mm+bf16+dotprod"`.
- Rebuild from the same source, commit, compiler, and host.
- Verify the result with L1 symbol counts and the runtime selection log.

**Measured impact**

- 10 `kai_run_matmul` entry points are present.
- 7B prefill: 48.64 → 222.14 tok/s, **4.57x**.
- 7B decode: 11.17 → 18.45 tok/s, **1.65x**.
- Negative control: 0.5B decode is **0.99x**, no measurable effect.

**Authoritative paths**

- `results/server/spark-provenance.txt`
- `results/scale/scale-experiment.json`
- `docs/UPSTREAM-ISSUE-FINDING3.md`
- `docs/PRODUCT.md` for the stock-release scope limitation

**Do not claim**

- that stock `llama.cpp` releases have this defect;
- that KleidiAI is generally 4.57x faster;
- that this reproduces across compilers or every Arm CPU; or
- that the diagnostic compiler-probe narrative is stronger than the captured build/banner/symbol
  evidence.

### E2 — generation-only auto-default patch

**Baseline**

- Unmodified no-flags generation/batch threads: 12/12.
- Decode: 67.8 tok/s.
- Prefill: 1,835.2 tok/s.
- Manual `-t 2` reaches the decode ceiling but reduces prefill to 975.6 tok/s, a 47% regression.

**Technical change**

- `patches/0002-kleidiai-sme-aware-thread-default.patch`.
- Read KleidiAI's runtime-detected SME2 thread cap.
- Change only an unspecified generation-thread default.
- Preserve batch/prefill default and explicit user choices.
- Provide `GGML_KLEIDIAI_AUTO_THREADS=0`.

**Measured impact**

- No-flags decode: 67.8 → 145.9 tok/s, **2.15x**.
- No-flags prefill: 1,835.2 → 1,779.8 tok/s, **-3.0% within noise**.
- Patched no-flags decode matches the hand-tuned ceiling within measurement noise.
- Negative result: at the measured 1.5B Q4_0 configuration, the SME-cap heuristic misses the
  best measured decode thread count by about 17.5%.

**Authoritative paths**

- `patches/0002-kleidiai-sme-aware-thread-default.patch`
- `patches/README.md`
- `results/AUTODEFAULTS.md`
- `results/GENERALIZATION.md`

**Do not claim**

- that 2.15x is universal;
- that the fixed cap is a per-model optimum; or
- that patch `0001` is part of this positive result.

### E3 — CUDA host buffer hides real KleidiAI dispatch

**Baseline**

- One binary built with CUDA and KleidiAI.
- CPU-only run uses `-ngl 0`.
- Banner, I8MM selection log, and 10/149 symbol count match the working arms.
- L3 records 0 KleidiAI hits in 5/5 reps.
- Polygraph exit code: `1`.

**Technical change**

- Run the same binary and model with `--no-host`, or with `-dev none`.

**Measured impact**

- `--no-host`: 7,968 hits in 5/5 reps, exit `0`.
- `-dev none`: 7,968 hits in 5/5 reps, exit `0`.
- No timing/performance impact is claimed.

**Authoritative paths**

- `results/upstream/llamacpp-26334-cuda-host-buffer.json`
- `results/upstream/FINDING-4-CUDA-HOST-BUFFER.md`
- `tools/targets/llama-cpp-kleidiai-cuda-ngl0-*.json`

**Attribution boundary**

`izard` reported and diagnosed the mechanism in upstream issue `#26334`. Polygraph contributes
the measured 15-run debugger reproduction, not discovery priority.

### E4 — Arm64 production-shaped readiness candidate

**Baseline**

- Prior contest evidence ended at dispatch, correctness, and throughput tests.
- No sustained mixed server load, per-request rows, one-second telemetry, or controlled restart
  gate was preserved as one artifact.

**Technical change**

- Add `tools/server_readiness.py`.
- Add the manual `server-readiness` job to `.github/workflows/verify-free-arm64.yml`.
- Pin Qwen2.5 1.5B Q4_0 and `llama.cpp`.
- Capacity: concurrency 1/2/4.
- Soak: concurrency 4 for 600 measured seconds.
- Mixed short-chat, RAG-like, and longer-summary traffic.
- Two controlled restart cycles.
- Ten explicit checks.
- Preserve JSONL, telemetry, logs, checksums, workflow receipt, validation receipt, and three-model
  contest matrix.

**Measured impact**

- GitHub Actions run: `31294460364`.
- Commit: `97f1c210dc0e3435d04f8082a362e291d409a261`.
- Runner: GitHub-hosted `ubuntu-24.04-arm`, aarch64, 4 CPUs.
- Measured requests: 1,543.
- Failures: 0.
- Soak output: 55.45 tok/s.
- Soak TTFT p99: 748.46 ms.
- Soak E2E p99: 1,924.57 ms.
- Maximum RSS: 1,869.69 MiB.
- Slower restart readiness: 1.5123 seconds.
- All 10 configured checks passed.

**Authoritative paths**

- `results/production-readiness/arm64-31294460364/README.md`
- `results/production-readiness/arm64-31294460364/summary.json`
- `results/production-readiness/arm64-31294460364/measured-requests.jsonl`
- `results/production-readiness/arm64-31294460364/warmup-requests.jsonl`
- `results/production-readiness/arm64-31294460364/telemetry.csv`
- `results/production-readiness/arm64-31294460364/llama-server.log`
- `results/production-readiness/arm64-31294460364/sha256sums.txt`
- `results/production-readiness/arm64-31294460364/package-sha256sums.txt`
- `results/production-readiness/arm64-31294460364/workflow-receipt.json`
- `results/production-readiness/arm64-31294460364/validation-receipt.json`
- `results/production-readiness/arm64-31294460364/contest-matrix/`

**Claim boundary**

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

### E5 — same-artifact long Arm64 temporal-control campaign

**Baseline**

- The first Arm64 readiness artifact preserves a 10-minute soak and two controlled restarts.
- It does not test the deterministic paired replay and rollback-decision machinery over a larger
  aggregate synthetic exposure.
- A baseline/candidate label could be misread as an optimization comparison unless artifact
  identity and the claim ceiling are made explicit.

**Technical change**

- Add the main-only `.github/workflows/verify-production-arm64.yml` lane.
- Pin source commit `5833ff20f503d126a8654f127419ed1e27ce2f5d`, reviewed `llama.cpp` commit
  `dbadb68eecdfb3ab0e86872d011738fc937f0364`, and the Qwen2.5 1.5B Q4_0 model hash.
- Use the exact same `llama-server` and model artifacts for the baseline and temporal-candidate
  labels.
- Generate deterministic synthetic traces with shard-scoped labels; do not replay production
  traffic or PII.
- Exercise capacity, readiness, replay, controlled process restart/model reload, deterministic
  load-generator delay/error, and rollback-decision checks.
- Run two independent long shards and preserve complete strict-verifier receipts, build
  provenance, original GitHub ZIP digests, workflow metadata, and an independently recomputed
  aggregate.

**Measured impact**

- Smoke preflight run `31312308726`: 38 measured requests, 0 measured failures, 8 aggregate
  measured seconds, 4-second longest continuous segment, `PASS` / `KEEP_CANDIDATE`.
- Long run `31312723300`: 79,684 measured requests and 0 measured failures.
- Long duration: 36,000 aggregate measured seconds (**10 aggregate hours**) across two shards.
- Longest continuous segment: 9,000 seconds (**2.5 hours**).
- Both long shards: `PASS` / `KEEP_CANDIDATE`, identical paired trace digests, and
  `sameArtifactControl:true`.
- No optimization or uplift is claimed because the baseline and candidate artifacts are
  identical.

**Authoritative paths**

- `results/production-readiness/arm64-campaign-31312308726-31312723300/README.md`
- `results/production-readiness/arm64-campaign-31312308726-31312723300/long-aggregate-receipt.json`
- `results/production-readiness/arm64-campaign-31312308726-31312723300/long-shard-1-receipt.json`
- `results/production-readiness/arm64-campaign-31312308726-31312723300/long-shard-2-receipt.json`
- `results/production-readiness/arm64-campaign-31312308726-31312723300/long-validation-receipt.json`
- `results/production-readiness/arm64-campaign-31312308726-31312723300/package-sha256sums.txt`
- immutable release `arm-create-evidence-31312723300` for the original GitHub artifact ZIPs and
  build-provenance bundles

**Claim boundary**

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
deploymentAuthorized:false
tenantIsolationClaimed:false
continuousAvailabilityClaimed:false
```

### E6 — RTX PRO 6000 non-Arm systems control

**Baseline**

- The contest-relevant readiness candidate is a single Qwen2.5 1.5B Q4_0 Arm64 host.
- It cannot establish that the artifact method was exercised with a much larger model and a
  different serving runtime.

**Technical change**

- Run an isolated production-shaped control on owned x86_64/CUDA hardware.
- Model: `Qwen/Qwen3-30B-A3B-Instruct-2507`.
- Immutable revision: `0d7cf23991f47feeb3a57ecb4c9cee8ea4a17bfe`.
- Runtime: vLLM.
- Capacity: concurrency 1/4/8/16.
- Soak: concurrency 8 for 900 measured seconds.
- Preserve request JSONL, telemetry, preflight receipts, logs, workflow boundary, and checksums.

**Measured impact**

- Run: `31289517517`.
- Measured requests: 7,573.
- Measured failures: 0.
- Warm-up requests: 1,071, all successful.
- Usage accounting missing: 0.

**Authoritative paths**

- `results/production-readiness/pro6000-31289517517/README.md`
- `results/production-readiness/pro6000-31289517517/aggregate.json`
- `results/production-readiness/pro6000-31289517517/capacity-sweep/summary.json`
- `results/production-readiness/pro6000-31289517517/soak-c8/summary.json`
- `results/production-readiness/pro6000-31289517517/workflow-receipt.json`
- `results/production-readiness/pro6000-31289517517/sha256sums.txt`

**Control boundary**

```text
actualProductionTraffic:false
armContestEvidence:false
candidateOnly:true
registeredResult:false
```

The aggregate does not contain `productionReady`; no production-ready status is inferred.

### E7 — claim-integrity and negative-result discipline

**Baseline**

- An early +57.3% result was wrong and is retracted.
- Patch `0001` changed dispatch but measured about 12% slower.
- A headline tuning multiple shrank materially at a larger model size.

**Technical change**

- Round-robin interleaved remeasurement.
- Public correction.
- `docs/CLAIMS.md` registry.
- `tools/check_claims.py` gate.
- Toolchain unit tests.
- Negative and non-generalizing outcomes retained beside positive claims.

**Measured impact**

- Patch `0002` has the narrower positive result.
- Patch `0001` remains negative.
- 0.5B Finding 3 decode remains a 0.99x null result.
- The 0.5B-to-7B tuning comparison remains 4.56x to 1.33x rather than a universal claim.

**Authoritative paths**

- `results/REMEASURE-2026-08-04-QUIET.md`
- `results/AUTODEFAULTS.md`
- `results/scale/scale-experiment.json`
- `results/GENERALIZATION.md`
- `docs/CLAIMS.md`
- `tools/check_claims.py`
- `.github/workflows/claims.yml`
- `tests/test_check_claims.py`

## Contest criteria map

| Criterion | Weight | Primary proof | Secondary proof | What not to overclaim |
|---|---:|---|---|---|
| Technological Implementation | 40 | L1/L2/L3 verifier, fail-closed CLI, ground-truth tests, Arm64 readiness harness | MCP server, target schema, cross-platform debugger support, claims gate | L1/L2 alone are not dispatch proof |
| "WOW" factor | 25 | `KLEIDIAI = 1` with 0 usable matmul entry points; 4.57x measured 7B prefill comparison | 0 vs 7,968 L3 hits while banner/log/symbols match | Not a universal KleidiAI multiple |
| Potential Impact | 20 | Public generic verifier, upstream reports, reusable CI pattern | 15-run reproduction of independent issue, public Apache-2.0 repository | Finding 3 does not affect stock releases |
| UX / DX | 15 | `make demo`, one CLI, JSON, exit codes 0/1/2 | Free hosted Arm64 workflow, quickstart, presets, ad-hoc mode | Missing debugger is undetermined, not success |

## Reproduction commands

### Product demo and claim gate

```bash
git clone https://github.com/tomyimkc/polygraph.git
cd polygraph
make demo
python3 tools/check_claims.py
```

### Arm64 artifact integrity and headline values

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
jq '{headSha, checks, contestMatrix}' validation-receipt.json
jq '{runId, headSha, conclusion, url, jobs, artifacts}' workflow-receipt.json
```

### Same-artifact long Arm64 campaign

```bash
cd results/production-readiness/arm64-campaign-31312308726-31312723300
sha256sum -c package-sha256sums.txt
jq '{
  actualProductionTraffic,
  productionReady,
  candidateOnly,
  aggregateMeasuredSoakSeconds,
  longestContinuousSoakSegmentSeconds,
  shards
}' long-aggregate-receipt.json
jq '{source, checks, durations, totals, shards, boundary}' long-validation-receipt.json
```

Read `aggregateMeasuredSoakSeconds:36000` as **10 aggregate measured hours**, not continuous
availability. The authoritative longest continuous segment is
`longestContinuousSoakSegmentSeconds:9000` (**2.5 hours**).

### Non-Arm control integrity and boundary

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
```

### Free hosted Arm64 rerun

```bash
gh workflow run verify-free-arm64.yml --repo OWNER/polygraph
gh run list --repo OWNER/polygraph --workflow verify-free-arm64.yml --limit 1
gh run watch --repo OWNER/polygraph RUN_ID --exit-status
```

### Zero-kernel build pair

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

### CUDA-host-buffer mismatch and correction

The dedicated presets are the reproducible interface. `BINARY` must point to the measured class of
CUDA+KleidiAI `llama-cli` build, and `MODEL` must point to the measured `q05.gguf`:

```bash
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

## Repository-native final video receipt

| Artifact | Current validated value |
|---|---|
| Final MP4 | `demo/out/polygraph-contest-final.mp4` |
| Duration | 169.021333 seconds (2:49.021), 10.978667 seconds under the cap |
| Size | 9,239,731 bytes |
| SHA-256 | `0deb7f67697b45ac8b9b38b518d87240ac556db8bb82107f23158862372f8f30` |
| Captions | `demo/out/polygraph-contest-final.srt`, six authored cues |
| Validation receipt | `demo/out/polygraph-contest-final.validation.json` |
| Checksum | `demo/out/polygraph-contest-final.sha256` |
| Media | H.264 High, 1920×1080, 30 fps, `yuv420p`; AAC-LC, 48 kHz stereo; fast-start |

The validation receipt records the final MP4 SHA-256, story hash, encoded media properties, and
SHA-256 hashes for the evidence sources used by the renderer. The final cut contains a 20-second
playback of a real, isolated `make demo` capture. The capture receipt verifies the strict
allowlisted environment, source archive and capture-driver hashes, bounded process/output policy,
transcript reconstruction, and the liar/honest L3 and exit-code contract. Captured text and
ordering are unchanged; pauses are normalized for legibility.

## Negative-result index

| Negative or limiting result | Evidence |
|---|---|
| Retracted +57.3% result caused by non-interleaved contention | `results/REMEASURE-2026-08-04-QUIET.md` |
| Patch `0001` is about 12% slower at the measured default thread count | `patches/README.md`, related results |
| Finding 3 at 0.5B decode is 0.99x, no measurable effect | `results/scale/scale-experiment.json` |
| Tuning result shrinks from 4.56x at 0.5B to 1.33x at 7B | `results/scale/scale-experiment.json` |
| Patch `0002` fixed cap misses the measured 1.5B optimum by about 17.5% | `results/GENERALIZATION.md` |
| Arm64 readiness is synthetic and single-host | `results/production-readiness/arm64-31294460364/summary.json` |
| Long Arm64 campaign is aggregate, same-artifact synthetic evidence — not 10 continuous hours and not uplift | `results/production-readiness/arm64-campaign-31312308726-31312723300/long-validation-receipt.json` |
| PRO 6000 control is not Arm evidence | `results/production-readiness/pro6000-31289517517/aggregate.json` |
| Finding 3 does not affect stock release binaries | `docs/PRODUCT.md`, `results/prevalence/shipped-binaries-2026-08-05.json` |
| Automated Spark lane remains best-effort | `README.md`, `.github/workflows/verify-spark-aarch64.yml` |

## Public-source and license map

- Repository: `https://github.com/tomyimkc/polygraph`
- Project license: `LICENSE` — Apache License 2.0.
- `llama.cpp`: instrumented at pinned commit; upstream MIT license.
- KleidiAI: used through `llama.cpp`'s backend.
- Patch files: diffs against MIT-licensed `llama.cpp` files; licensing boundary documented in
  `patches/README.md`.
- Qwen2.5 demo models used by the Arm evidence: Apache-2.0, hashes pinned in repository manifests
  and evidence.

## Final judge index

| Judge need | Open this first |
|---|---|
| Understand the product in two minutes | `docs/QUICKSTART.md`; run `make demo` |
| Verify the headline defect | `results/server/spark-provenance.txt` |
| Verify the 7B impact | `results/scale/scale-experiment.json` |
| Inspect the positive patch | `results/AUTODEFAULTS.md` and `patches/0002-kleidiai-sme-aware-thread-default.patch` |
| See why L3 is necessary | `results/upstream/FINDING-4-CUDA-HOST-BUFFER.md` |
| Audit Arm64 run `31294460364` | `results/production-readiness/arm64-31294460364/` |
| Audit long campaign `31312723300` | `results/production-readiness/arm64-campaign-31312308726-31312723300/` |
| Audit control `31289517517` | `results/production-readiness/pro6000-31289517517/` |
| Audit the final contest video | `demo/out/polygraph-contest-final.validation.json`, `.sha256`, and `.srt` |
| Inspect corrections and negative results | `results/REMEASURE-2026-08-04-QUIET.md`, `results/GENERALIZATION.md`, `patches/README.md` |
| Verify prose claims mechanically | `python3 tools/check_claims.py` |
| Paste the final submission | `docs/DEVPOST-SUBMISSION.md` |
| Complete owner-only steps | `docs/CONTEST-SUBMISSION-CHECKLIST.md` |
