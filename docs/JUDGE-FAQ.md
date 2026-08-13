<!-- SPDX-License-Identifier: Apache-2.0 -->

# Judge FAQ — scope, attribution, and Cloud AI fit

This page states the strongest claims the evidence supports and answers the objections a skeptical
judge should raise. It is intentionally stricter than a marketing FAQ.

## Is Polygraph claiming that an Arm chip lies about performance?

No. The hardware is not the speaker. The potentially misleading signal comes from a software
layer: a compiler feature probe, build configuration, startup banner, runtime dispatcher, or an
interpretation of benchmark output.

Polygraph checks whether those software claims match the binary and the measured execution. The
headline case is therefore described as a **software build/runtime verification failure on an Arm
platform**, not dishonesty by the Arm CPU.

## What does the L1/L2/L3 verifier prove?

- **L1 — static:** matching accelerated-kernel symbols exist in the built artifact.
- **L2 — selection:** the runtime reports selecting a particular kernel family.
- **L3 — dispatch:** the measured workload entered matching machine-code entry points.

Together they prove execution provenance for the measured binary and workload. They do **not**
prove that the selected path was faster, optimal, energy-efficient, production-ready, or correct
for every input.

Those are separate questions. Polygraph measures performance with controlled paired server tests
and keeps readiness and deployment authorization as explicit, independent gates.

## Why does this fit the Cloud AI track?

The contest implementation targets `llama-server` on Arm64 and treats acceleration verification as
a cloud deployment-control problem:

1. verify the server binary actually contains the intended Arm/KleidiAI path;
2. prove the measured inference workload dispatched into that path;
3. measure concurrent throughput, time to first token, end-to-end latency, and peak RSS;
4. test sustained synthetic load and controlled restart/readiness behavior; and
5. return a machine-readable keep-or-rollback decision.

A silent fallback in a cloud fleet can invalidate capacity planning, latency budgets,
memory-per-instance assumptions, and rollout comparisons even when the service remains
functionally correct. Polygraph is designed to fail CI or a pre-deployment gate before that
misinterpretation becomes a fleet-wide assumption.

The measured path relevant to the Arm submission is **Arm CPU inference through KleidiAI**. The
host named DGX Spark also contains GPU capability, but the KleidiAI finding is about its Arm
Cortex-X925/A725 CPU. The automated hosted Arm64 readiness lane explicitly launches
`llama-server` with `-ngl 0`, and the fixed server build enables the CPU KleidiAI backend rather
than CUDA.

## Does a debugger dispatch count prove a performance improvement?

No. Dispatch proof and performance proof are deliberately separate:

| Question | Evidence |
|---|---|
| Did the intended path run? | L1/L2/L3 symbols, selection, and debugger hit counts |
| Was it faster under the tested load? | Interleaved or paired benchmark measurements |
| Is the candidate safe to promote? | Configured throughput, latency, memory, recovery, provenance, and boundary checks |

This separation is central to the project. A path can execute and still be slower. Patch `0002`
demonstrated exactly that risk: an isolated local result looked favorable, but the stricter
distinct-binary Arm64 server campaign missed its promotion threshold and returned
`ROLLBACK_TO_BASELINE`.

## Is the 4.57x comparison a new Polygraph optimization?

No. It is the measured difference between one broken native-build configuration and its corrected
feature-target build on one Arm system. Polygraph diagnosed and verified the correction; it did
not invent a new matmul kernel.

The result must not be described as:

- a universal KleidiAI speedup;
- performance available to every Arm application;
- a cross-hardware comparison;
- evidence that stock `llama.cpp` release binaries are affected; or
- proof that every compiler or Arm CPU reproduces the same build failure.

The contribution is the fail-closed verifier, the reproducible evidence chain, and the
pre-deployment keep/rollback workflow.

## Did the project's optimization candidate succeed?

It produced a recorded isolated result, but it did **not** pass the stronger cloud promotion gate.
The paired `llama-server` campaign recorded 0.9330x median candidate/baseline throughput and
returned `FAIL / ROLLBACK_TO_BASELINE`.

That negative result is not hidden. It demonstrates why execution provenance alone is
insufficient and why an optimization should not be promoted from a favorable local benchmark.

## Is `make demo` the Arm contest evidence?

No. `make demo` is a portable, model-free fixture that lets a judge verify the CLI contract and
see how identical banners can produce different dispatch verdicts. It needs no Arm hardware and
does not claim to benchmark Arm.

The Arm contest evidence is in the committed receipts linked from
`docs/CONTEST-EVIDENCE-MAP.md`, including the Arm build provenance, real dispatch counts,
concurrent `llama-server` measurements, hosted Arm64 readiness artifact, and paired rollback
receipt.

## Is this production-ready?

No production-readiness or live-traffic claim is made. The authoritative Arm64 boundary is:

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

The evidence is production-shaped but synthetic and single-host. It does not establish multi-day
availability, network or host failover, tenant isolation, application-specific answer quality, or
deployment authorization.

## Who is actually affected by the zero-kernel finding?

The demonstrated affected population is the tested native source-build configuration and similar
release-pipeline/build-engineering situations where feature detection can disagree with the
target CPU. The repository's stock-release audit did not find the same zero-kernel defect in the
official `llama.cpp` Arm64 release variants it inspected.

Polygraph should therefore be positioned for framework maintainers, source builders, CI/release
engineers, and cloud inference operators—not as proof that ordinary users of every packaged
runtime currently have this defect.

## What was created or meaningfully updated during the challenge?

The contest work includes the Polygraph verifier and CLI, built-in target definitions, debugger
dispatch collection, Arm/KleidiAI investigations, server/readiness and paired campaign harnesses,
machine-readable receipts, claim-lint tooling, judge demo, documentation, and upstream reports.
Repository history and immutable evidence releases provide the timing and provenance record.

## Short defensible description

> Polygraph is a fail-closed verification and deployment gate for Arm64 cloud inference. It proves
> whether the accelerated kernels advertised by an AI runtime were compiled, selected, and
> executed, then separately checks whether a candidate satisfies controlled server-performance
> and recovery gates. It found one broken `llama.cpp` KleidiAI source build, verified the corrected
> path, and later rejected its own optimization candidate when the stronger paired gate did not
> hold.
