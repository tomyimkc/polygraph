<!-- SPDX-License-Identifier: Apache-2.0 -->

# Arm64 distinct-artifact auto-default confirmation

Measured on 2026-08-11 on an Apple M4 Max (`Mac16,5`, `arm64`) using:

- baseline: unmodified `llama.cpp` at
  `dbadb68eecdfb3ab0e86872d011738fc937f0364`;
- candidate: the same commit plus
  `patches/0002-kleidiai-sme-aware-thread-default.patch`;
- model: Qwen2.5 0.5B Instruct Q4_0, SHA-256
  `7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed`;
- distinct server artifacts, with no build-tree llama/ggml dynamic-library dependency, and
  SHA-256
  `af24708d50178d2b6713756f0189b08b4431bc20e5facbce1b82cf5ee4336186`
  and
  `c383a672c5fb427281b93483f0bdbc14a6f612af38cb2b7637ba6a2148395363`;
- campaign source
  `92267687b1d999c68c209d66ddd4bde74f570cec`.

## Claim boundary

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
deploymentAuthorized:false
```

This is single-host, deterministic synthetic evidence collected on a shared machine. It does not
prove production readiness, live-traffic correctness, tenant isolation, failover, or availability.
Alternating AB/BA order reduces monotonic host-load bias; it does not turn a shared host into a
controlled lab.

## Protocol

- three paired repetitions;
- 180 aggregate measured soak seconds, 30 seconds per arm per repetition;
- `serverThreadPolicy=binary-default`, so neither arm receives `-t` or `-tb`;
- `armOrderPolicy=alternating`, producing AB/BA/AB execution;
- identical deterministic trace digest inside each paired round;
- capacity, soak, one-second RSS telemetry, controlled restart, and synthetic replay per arm;
- median of within-round candidate/baseline ratios;
- `requireThroughputUplift=true`, binding promotion to throughput ratio `>= 1.0`.

All three baseline and candidate readiness measurements were valid, all candidate readiness and
replay gates passed, all expected injected errors were observed, and the 381 measured requests
recorded zero measured failures. The two server binaries were distinct, so this was not a
same-artifact control.

## Result

| paired metric | round 1 | round 2 | round 3 | median |
|---|---:|---:|---:|---:|
| candidate throughput / baseline | 1.0678 | 0.9330 | 0.5598 | **0.9330** |
| candidate E2E p99 / baseline | 0.8557 | 1.2464 | 1.4720 | **1.2464** |
| candidate TTFT p99 / baseline | 0.5625 | 1.7081 | 2.6043 | **1.7081** |

The throughput gate failed:

```text
gateVerdict: FAIL
rollbackVerdict: ROLLBACK_TO_BASELINE
```

Patch `0002` produced an isolated `llama-cli` win in `results/AUTODEFAULTS.md`, but this stronger
server-shaped confirmation did not support promoting its fixed 2-thread heuristic. The reusable
positive result is the measurement discipline: real binary defaults, distinct artifact hashes,
AB/BA execution, paired-round scoring, and a fail-closed promotion threshold rejected a candidate
that a weaker aggregate comparison could have made look favorable.

## Files and verification

- `receipt.json`: authoritative campaign result and paired metrics;
- `resolved-config.json`: exact profile, model identity, SLOs, and claim flags;
- `build-provenance.json`: host, toolchain, build settings, source, patch, artifact, and model
  identities;
- `round-*/`: raw readiness, telemetry, replay, restart, invocation, and trace receipts;
- `sha256sums.txt`: complete checksum manifest.

Verify checksums and schema:

```bash
python3 tools/production_campaign.py verify \
  --out-dir results/production-readiness/arm64-0.5b-autodefault-differential-confirmation-20260811
```

The expected `verify` exit status is `0` because the checksum set and receipt contract are valid.
Verification does not turn a rollback into a pass: the authoritative receipt must still retain
`gateVerdict:FAIL` and `rollbackVerdict:ROLLBACK_TO_BASELINE`. The original `run` command exited
`1` for that measured rollback.
