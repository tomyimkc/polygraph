<!-- SPDX-License-Identifier: Apache-2.0 -->

# Arm Create submission checklist

Use this immediately before the final Devpost submission. The checklist is intentionally
claim-boundary-heavy: a polished submission that silently turns synthetic evidence into a
production claim would be worse than an incomplete submission.

## 1. Repository and branch preflight

- [ ] Work from the intended contest-writing branch/worktree.
- [ ] Confirm only intended contest files changed:

  ```bash
  git status --short
  git diff -- docs/DEVPOST-SUBMISSION.md \
    docs/CONTEST-EVIDENCE-MAP.md \
    docs/CONTEST-SUBMISSION-CHECKLIST.md
  ```

- [ ] Confirm the public repository field is
  `https://github.com/tomyimkc/polygraph`.
- [ ] Confirm `LICENSE` is Apache License 2.0.
- [ ] Do not describe `llama.cpp` patch files as relicensed Apache-2.0 source; preserve the MIT
  derivative-work boundary documented in `patches/README.md`.

## 2. Required claim boundary

- [ ] The submission contains the exact Arm64 boundary:

  ```text
  actualProductionTraffic:false
  productionReady:false
  candidateOnly:true
  ```

- [ ] Run `31294460364` is called **production-shaped synthetic Arm64 evidence**, not production
  traffic and not a production-ready deployment.
- [ ] Run `31289517517` is called a **non-Arm RTX PRO 6000 systems control**.
- [ ] The PRO 6000 control remains explicitly:

  ```text
  actualProductionTraffic:false
  armContestEvidence:false
  candidateOnly:true
  registeredResult:false
  ```

- [ ] Do not invent `productionReady:false` as a field inside the PRO 6000 aggregate; that key is
  absent. State instead that the submission does not infer production readiness from the control.
- [ ] Do not compute or quote an Arm-versus-PRO-6000 speed ratio.

## 3. Baseline → technical change → measured impact structure

For every judge-facing proof chain, verify all three parts are present.

### Headline zero-kernel build finding

- [ ] **Baseline:** `KLEIDIAI = 1`, 0 usable matmul entry points, broken-build throughput.
- [ ] **Technical change:** explicit `GGML_NATIVE=OFF` and `GGML_CPU_ARM_ARCH` feature-target pair.
- [ ] **Measured impact:** 7B prefill 48.64 → 222.14 tok/s, 4.57x; decode 11.17 → 18.45 tok/s,
  1.65x.
- [ ] **Negative result:** 0.5B decode remains 0.99x, no measurable effect.
- [ ] Scope says this is one measured build condition and does not affect stock releases.

### Positive patch `0002`

- [ ] **Baseline:** no-flags generation/batch defaults and the manual `-t 2` prefill trap.
- [ ] **Technical change:** generation-only auto-default to the runtime-detected SME cap, preserving
  batch/prefill and explicit choices.
- [ ] **Measured impact:** 67.8 → 145.9 tok/s decode, 2.15x; prefill -3.0% within noise.
- [ ] **Negative result:** the fixed cap misses the measured 1.5B optimum by about 17.5%.

### CUDA-host-buffer dispatch mismatch

- [ ] **Baseline:** banner/log/symbol count match, L3 records 0, exit `1`.
- [ ] **Technical change:** `--no-host` or `-dev none`.
- [ ] **Measured impact:** 7,968 L3 hits, exit `0`, 5/5 reps.
- [ ] Reproduction uses the three dedicated presets:
  `llama-cpp-kleidiai-cuda-ngl0-baseline`,
  `llama-cpp-kleidiai-cuda-ngl0-nohost`, and
  `llama-cpp-kleidiai-cuda-ngl0-devnone`.
- [ ] Each preset receives
  `--binary build-cuda-kleidiai/bin/llama-cli --model q05.gguf`
  (or equivalent absolute paths), plus the recorded L2/L3 timeouts.
- [ ] No performance claim is attached.
- [ ] `izard` receives discovery/diagnosis credit.

### Arm64 readiness run

- [ ] **Baseline:** prior evidence stopped before sustained production-shaped serving and recovery.
- [ ] **Technical change:** capacity, 10-minute soak, mixed traffic, per-request rows, telemetry,
  two restarts, 10 checks.
- [ ] **Measured impact:** run `31294460364`, 1,543 measured requests, 0 failures, registered p99,
  RSS, and restart observations.
- [ ] The exact `false/false/true` boundary appears beside the PASS.

### PRO 6000 control

- [ ] **Baseline:** Arm candidate is narrow in model/runtime scale.
- [ ] **Technical change:** separate larger-model vLLM control with capacity and 15-minute soak.
- [ ] **Measured impact:** run `31289517517`, 7,573 measured requests, 0 failures.
- [ ] The control is explicitly not Arm contest evidence and not a production-ready claim.

## 4. Judging criteria

- [ ] **Technological Implementation — 40 points** appears first and receives the most space.
- [ ] **"WOW" factor — 25 points** centers the banner-versus-reality finding and self-audit.
- [ ] **Potential Impact — 20 points** covers upstream reports, generic CI use, public repo, and
  honest affected scope.
- [ ] **User Experience / Developer Experience — 15 points** covers `make demo`, JSON, exit codes,
  presets, ad-hoc mode, graceful degradation, and free Arm64 CI.
- [ ] No optimization-focus-area list is mistaken for the scored rubric.

## 5. Evidence checks

### Claims gate

- [ ] Run:

  ```bash
  python3 tools/check_claims.py
  ```

- [ ] Expected:

  ```text
  OK: every scanned numeric claim is registered/JSON-backed; no retracted figure appears unmarked.
  ```

### Arm64 artifact

- [ ] Run:

  ```bash
  (
    cd results/production-readiness/arm64-31294460364
    sha256sum -c sha256sums.txt
    test "$(wc -l < measured-requests.jsonl)" -eq 1543
    test "$(wc -l < warmup-requests.jsonl)" -eq 11
    jq -e '
      .actualProductionTraffic == false and
      .productionReady == false and
      .candidateOnly == true and
      .gate.verdict == "PASS" and
      .totals.measuredRequests == 1543 and
      .totals.measuredFailures == 0
    ' summary.json
    jq -e '
      .checks.measuredRowsMatchSummary == true and
      .checks.originalArtifactChecksums == true and
      .checks.allWorkflowJobsSuccessful == true
    ' validation-receipt.json
  )
  ```

- [ ] Confirm `workflow-receipt.json` points to run `31294460364`, head
  `97f1c210dc0e3435d04f8082a362e291d409a261`, and a successful conclusion.

### PRO 6000 control

- [ ] Run:

  ```bash
  (
    cd results/production-readiness/pro6000-31289517517
    sha256sum -c sha256sums.txt
    test "$(
      wc -l capacity-sweep/requests-c*.jsonl soak-c8/requests-c8.jsonl |
        tail -1 |
        awk '{print $1}'
    )" -eq 7573
    jq -e '
      .runId == "31289517517" and
      .actualProductionTraffic == false and
      .armContestEvidence == false and
      .candidateOnly == true and
      .registeredResult == false and
      .totals.measuredRequests == 7573 and
      .totals.measuredFailures == 0
    ' aggregate.json
  )
  ```

## 6. Negative results must remain visible

- [ ] The retracted +57.3% result is described as retracted, not silently removed.
- [ ] Patch `0001` remains a measured regression, not a promoted optimization.
- [ ] The 0.99x 0.5B decode null result remains visible.
- [ ] The 4.56x → 1.33x model-size shrinkage remains visible.
- [ ] Patch `0002`'s model-dependent thread-optimum limitation remains visible.
- [ ] Finding 3's stock-release non-impact remains visible.
- [ ] Automated Spark lane limitations remain visible.
- [ ] No upstream issue is described as accepted, fixed, or endorsed without new evidence.

## 7. Demo video

- [ ] Use the repository-native final cut:
  `demo/out/polygraph-contest-final.mp4`.
- [ ] Confirm the independent `ffprobe` duration is 169.021333 seconds (2:49.021), strictly under
  three minutes.
- [ ] Confirm the final file is 9,527,083 bytes with SHA-256
  `076de488a8939f56278655a0775775257c7537317697aa3003d4aaf380ed18e4`.
- [ ] Confirm the authored six-cue sidecar is:
  `demo/out/polygraph-contest-final.srt`.
- [ ] Confirm the validation receipt is:
  `demo/out/polygraph-contest-final.validation.json`.
- [ ] Confirm the checksum file is:
  `demo/out/polygraph-contest-final.sha256`.
- [ ] Run:

  ```bash
  (
    cd demo/out
    sha256sum -c polygraph-contest-final.sha256
  )

  ffprobe -v error \
    -show_entries \
    format=duration,size:stream=index,codec_name,profile,width,height,pix_fmt,r_frame_rate,sample_rate,channels \
    -of json \
    demo/out/polygraph-contest-final.mp4

  jq -e '
    .schema == "polygraph.contest-video.validation.v1" and
    .sha256 == "076de488a8939f56278655a0775775257c7537317697aa3003d4aaf380ed18e4" and
    .media.durationSeconds == 169.021333 and
    .media.sizeBytes == 9527083 and
    .media.under180Seconds == true and
    .media.video.codec_name == "h264" and
    .media.video.width == 1920 and
    .media.video.height == 1080 and
    .media.audio.codec_name == "aac" and
    .media.fastStart == true
  ' demo/out/polygraph-contest-final.validation.json
  ```

- [ ] Confirm every on-screen number remains registered in `docs/CLAIMS.md`.
- [ ] Paste the YouTube URL into the Devpost demo field.
- [ ] Confirm the final cut includes the Arm64 run `31294460364` and labels the PRO 6000 control
  non-Arm, as recorded by the story and validation receipt.
- [ ] Confirm the receipt-verified 20-second `make demo` playback is present and visibly shows the
  liar `MISMATCH`/zero-hit result and honest `MATCH`/one-hit result.
- [ ] Describe the capture precisely: real isolated CLI output with unchanged captured text and
  ordering, replayed with normalized pauses for legibility.
- [ ] Do not edit or overwrite `docs/VIDEO.md`, `docs/VIDEO-PRODUCTION.md`, `demo/README.md`,
  `demo/SHOTLIST.md`, renderer sources, or `demo/out/` from this contest-writing task.

## 8. Paste-ready Devpost fields

- [ ] Project name: `Polygraph`.
- [ ] Tagline copied from `docs/DEVPOST-SUBMISSION.md`.
- [ ] Project Overview copied from the final paste-ready block.
- [ ] Functionality / Output copied from the final paste-ready block.
- [ ] Setup Instructions copied from the final paste-ready block.
- [ ] Why it should win copied from the final paste-ready block.
- [ ] Judge evidence index copied from the final paste-ready block.
- [ ] Built-with tags include Arm KleidiAI, SME2, SVE2, NEON/I8MM/DOTPROD, `llama.cpp`, C/C++,
  Python, Bash, CMake, `lldb`, `gdb`, GitHub Actions, JSONL, and MCP.
- [ ] Repository URL is the public `tomyimkc/polygraph` URL.
- [ ] License is listed as Apache-2.0 with the patch/upstream attribution caveat.
- [ ] Primary upstream issue field points to `#26630`.
- [ ] Related narrative links `#26547` and credits `#26334`.

## 9. Judge-readable formatting

- [ ] The first screen answers: what is it, what did it find, what changed, what was measured.
- [ ] Every long section starts with Baseline, Technical change, and Measured impact.
- [ ] Tables fit without horizontal scrolling in the Devpost editor.
- [ ] Raw paths are code-formatted and remain clickable after paste where Devpost supports links.
- [ ] No paragraph starts with unexplained KleidiAI/SME2 jargon before the plain-language claim.
- [ ] The claim boundary is visible before the first readiness PASS.
- [ ] The PRO 6000 control is visually separated from Arm contest evidence.
- [ ] Negative results are not below a collapsed section or hidden after owner-only survey fields.

## 10. Owner-only final actions

- [ ] Upload `demo/out/polygraph-contest-final.mp4` to YouTube.
- [ ] Upload or paste the authored captions.
- [ ] Paste the final video URL.
- [ ] Verify the public GitHub repository opens in a logged-out/private browser session.
- [ ] Verify the dashboard URL opens if included; remove it from Devpost if not publicly reachable.
- [ ] Verify both upstream issue links open.
- [ ] Preview the entire Devpost submission on desktop and mobile widths.
- [ ] Save a local copy or screenshot of every final field before submission.
- [ ] Submit before the deadline shown in Devpost.
- [ ] Re-open the submitted project page and verify the rendered markdown, video, repo link,
  license, and evidence index.

## 11. Final automated sign-off

```bash
set -euo pipefail

python3 tools/check_claims.py

git diff --check -- \
  docs/DEVPOST-SUBMISSION.md \
  docs/CONTEST-EVIDENCE-MAP.md \
  docs/CONTEST-SUBMISSION-CHECKLIST.md

git status --short
```

Expected changed paths for this contest-writing task:

```text
docs/DEVPOST-SUBMISSION.md
docs/CONTEST-EVIDENCE-MAP.md
docs/CONTEST-SUBMISSION-CHECKLIST.md
```

Do not commit from this checklist unless the owner explicitly requests it.
