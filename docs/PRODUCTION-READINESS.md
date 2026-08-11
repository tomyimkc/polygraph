# Production-shaped Arm64 evidence campaign

`tools/production_campaign.py` is a fail-closed orchestration layer over
`tools/server_readiness.py`. It produces paired baseline/candidate evidence on
Arm64, but it is deliberately **not** a production-readiness claim or a
deployment gate.

Every top-level receipt retains these exact flags:

```json
{
  "actualProductionTraffic": false,
  "productionReady": false,
  "candidateOnly": true
}
```

## What the campaign measures

For every repetition, the orchestrator runs the existing server-readiness
capacity, soak, telemetry, and controlled-restart harness once for the baseline
and once for the candidate. It then starts each arm again for a deterministic
synthetic replay that adds:

- generated tenant labels such as `synthetic-tenant-alpha`;
- deterministic synthetic sentinels derived from the configured seed, shard,
  repetition, and label;
- no user-provided prompts, customer records, production logs, email addresses,
  phone numbers, or other PII;
- deterministic delay and expected-error injection **inside the load
  generator**, without pretending those errors came from a real network;
- controlled process termination and restart, which unloads and reloads the
  configured model;
- OpenAI-compatible chat replay with a request-specific JSON-schema `const`
  constraint, so the only valid completion is that request's own quoted
  sentinel;
- response scanning for another synthetic label's sentinel;
- rejection of any nominally successful response that is not exactly one valid
  JSON sentinel string or that omits its own sentinel.

The foreign-sentinel assertion covers only the generated trace in that run. It
does **not** establish real multi-tenant isolation, authorization boundaries,
storage isolation, or customer correctness. Raw model response text is not
written to the receipt; the campaign retains response length, digest, status,
latency, and sentinel observations.

The request-specific schema constraint makes this a deterministic
request/response binding canary across cache reuse and controlled restarts. It
is not an unconstrained instruction-following score and does not prove model
correctness. Replay has a separate 64-token response budget; the readiness
capacity/soak workload keeps its original profile-specific generation limits.

## Smoke and long duration semantics

The profiles live in `configs/production-readiness.json`.

| Profile | Purpose | Default paired measured soak budget | Default repetitions |
|---|---|---:|---:|
| `smoke` | CI plumbing, fault-path, receipt, and gate validation | 8 seconds total | 1 |
| `long` | Hosted Arm64 evidence with setup/upload headroom | 18,000 seconds total | 1 |

The measured soak budget is the **aggregate across both paired arms**. It is
divided evenly across `baseline × candidate × repetitions`. Therefore the
default long job has a longest continuous soak segment of 9,000 seconds, while
its paired aggregate measured soak budget is 18,000 seconds.

This distinction is intentional:

- the workflow does not advertise a literal six-hour continuous soak;
- the long job timeout is shorter than the hosted runner's practical ceiling
  and fails closed if setup, measurement, validation, or upload cannot finish;
- repetitions partition one shard's fixed measured-time budget into more
  paired samples;
- matrix shards are independent full campaign receipts, so two default long
  shards produce ten aggregate configured evidence hours while no individual
  receipt claims ten continuous hours;
- the aggregate verifier sums only complete, checksummed, passing shard
  receipts and reports the longest continuous segment separately.

Aggregate evidence hours are additive measurement duration, not continuous
availability, failover, or an uptime claim.

## Immutable hosted inputs and provenance

The hosted workflow accepts **no llama.cpp ref input**. It builds only the
reviewed exact commit:

```text
dbadb68eecdfb3ab0e86872d011738fc937f0364
```

The value must be lowercase 40-hex and must exactly match the tool's allowlist.
Branches, tags, short SHAs, option-like strings, and any other commit are
rejected. After the build, the workflow records `git rev-parse HEAD`, requires
the resolved value to equal the reviewed commit, and places both values in the
receipt. The workflow also rejects dispatches whose source ref is not
`refs/heads/main`.

All third-party Actions are pinned to reviewed full commit SHAs with their
human-readable versions left as comments. Every checkout uses
`persist-credentials:false`, checks out the event's exact source SHA, and
verifies `git rev-parse HEAD` before repository code runs.

The hosted model input is a choice backed by `scripts/models.txt`:

- `qwen2.5-1.5b-q4_0` is the default and the only model accepted by `long`;
- `qwen2.5-0.5b-q4_0` is explicitly a smoke-only option.

The planner resolves the selected row's Hugging Face repository, exact file,
SHA-256, and license. A missing row, duplicate row, malformed digest, unsafe
filename, or license other than `apache-2.0`/`mit` fails before the Arm job.
The fetch step uses that same `model_id`, writes to the manifest filename, and
rehashes the downloaded file. Model id/repository/file/digest/license and the
manifest digest are included in receipts and summaries. The model cache key
binds the model id, artifact digest, and manifest digest.

The reviewed llama.cpp source/build directory is deliberately not restored
from an Actions cache. Every shard requires that path to start absent, fetches
the fixed upstream repository at the reviewed commit, verifies the resolved
remote and SHA, rejects tracked/staged source drift, and then hashes the
resulting server executable.

## Artifact and receipt integrity

The campaign output directory must not exist before the run. The tool creates
it with mode `0700`; existing directories and symlink roots are rejected.
Checksum writing and verification reject:

- symlink files or directories anywhere under the output root;
- absolute or parent-traversal manifest entries;
- files whose resolved paths leave the output root;
- missing, extra, duplicate, malformed, or digest-mismatched entries.

Before and after every baseline/candidate arm, server start/stop, and controlled
restart, the campaign rehashes both the server executable and model. Any
size/digest change, disappearance, or replacement with a symlink makes artifact
integrity `UNDETERMINED`; the campaign emits `HOLD_UNDETERMINED`, never a
candidate-retention verdict.

`production_campaign.py verify` validates both checksums and the complete
receipt contract: schemas, exact claim flags, duration arithmetic, shard id,
configuration/SLO structure, model and artifact identities, immutable
llama.cpp provenance, main-branch hosted provenance, leakage fields, comparison
checks, gate verdict, and matching rollback verdict. The aggregate workflow
requires unique shard ids exactly equal to `1..N`.

## Explicit SLOs and rollback verdict

The config has separate explicit SLO profiles for smoke and long runs. The
readiness arm gates bind:

- measured request minimum;
- maximum measured error rate;
- soak TTFT and end-to-end p99 latency;
- restart success and maximum recovery time;
- usage/output completeness;
- maximum RSS;
- observed Arm64 architecture.

The replay gates bind:

- exact observation of planned load-generator errors;
- zero unexpected replay errors by default;
- replay end-to-end p99;
- zero foreign synthetic sentinels;
- zero malformed synthetic-sentinel responses;
- the own sentinel in every nominally successful response;
- exact process/model-restart count and successful recovery.

The paired comparison then checks identical trace digests, complete repetitions,
the configured aggregate soak budget, candidate error-rate delta, candidate
latency ratios, and candidate throughput ratio.

The receipt emits one of:

- `KEEP_CANDIDATE` — all configured synthetic gates passed;
- `ROLLBACK_TO_BASELINE` — the baseline was valid and the candidate regressed;
- `HOLD_UNDETERMINED` — infrastructure, an invalid baseline, or incomplete
  evidence prevents a valid comparison.

`KEEP_CANDIDATE` means only “retain this candidate for further evidence.” The
receipt always records `deploymentAuthorized:false`.

If baseline and candidate artifact hashes are identical, the receipt marks
`sameArtifactControl:true`. That is useful for exercising the comparison
machinery and temporal variability, but it is not evidence about a distinct
candidate.

## Distinct-artifact promotion controls

The historical campaign behavior remains unchanged by default:

- `serverThreadPolicy=explicit-host-count` passes the host CPU count as both
  `-t` and `-tb`;
- `armOrderPolicy=baseline-first` runs each baseline before its candidate; and
- the configured throughput floor can tolerate a bounded regression when the
  campaign is being used as a temporal control rather than promotion evidence.

For a real no-flags differential candidate, use all three stricter controls:

- `serverThreadPolicy=binary-default` omits `-t` and `-tb` from both readiness
  and replay, so each executable resolves its own defaults;
- `armOrderPolicy=alternating` runs AB/BA across repetitions, reducing bias from
  monotonic host load or thermal drift; and
- `requireThroughputUplift=true` raises the paired throughput floor to `1.0`.

Candidate latency and throughput are scored as the median of **within-round**
candidate/baseline ratios. The tool does not divide a candidate aggregate
extreme by a baseline aggregate extreme from another repetition. Every paired
round, trace digest, and execution position is retained in
`gate.metrics.pairedRounds`.

Baseline validity is also distinct from baseline SLO success. A candidate may
repair an absolute baseline latency or recovery SLO miss, but only when the
baseline still proves that the Arm workload ran, produced accountable non-empty
output, restarted, emitted process RSS, and completed replay integrity checks.
An invalid baseline remains `HOLD_UNDETERMINED`.

## Run locally

Build or provide executable baseline and candidate `llama-server` binaries plus
the exact manifest model file. The wrapper defaults to the reviewed llama.cpp
commit identity and the 1.5B manifest row:

```bash
BASELINE_SERVER=/path/to/baseline/llama-server \
BASELINE_MODEL=/path/to/baseline/qwen2.5-1.5b-instruct-q4_0.gguf \
CANDIDATE_SERVER=/path/to/candidate/llama-server \
CANDIDATE_MODEL=/path/to/candidate/qwen2.5-1.5b-instruct-q4_0.gguf \
PRODUCTION_MODEL_ID=qwen2.5-1.5b-q4_0 \
PRODUCTION_OUT=/tmp/polygraph-production-smoke \
bash scripts/lib/run_production_campaign.sh
```

The wrapper defaults to `smoke`. A bounded local override is explicit:

```bash
PRODUCTION_PROFILE=long \
PRODUCTION_REPETITIONS=2 \
PRODUCTION_SOAK_SECONDS_TOTAL=120 \
PRODUCTION_SHARD_ID=1 \
PRODUCTION_MODEL_ID=qwen2.5-1.5b-q4_0 \
PRODUCTION_SERVER_THREAD_POLICY=binary-default \
PRODUCTION_ARM_ORDER_POLICY=alternating \
PRODUCTION_REQUIRE_THROUGHPUT_UPLIFT=1 \
BASELINE_SERVER=/path/to/baseline/llama-server \
BASELINE_MODEL=/path/to/qwen2.5-1.5b-instruct-q4_0.gguf \
CANDIDATE_SERVER=/path/to/candidate/llama-server \
CANDIDATE_MODEL=/path/to/qwen2.5-1.5b-instruct-q4_0.gguf \
PRODUCTION_OUT=/tmp/polygraph-production-local \
bash scripts/lib/run_production_campaign.sh
```

Verify every file against the top-level checksum receipt:

```bash
python3 tools/production_campaign.py verify \
  --out-dir /tmp/polygraph-production-local
```

For `run`, exit status is `0` for `KEEP_CANDIDATE`, `1` for a measured
`ROLLBACK_TO_BASELINE`, and `2` for `HOLD_UNDETERMINED` or infrastructure/configuration failure.
For `verify`, exit status is `0` whenever the checksum set and receipt contract are valid,
including a valid measured rollback receipt; inspect `gate.gateVerdict` and
`gate.rollbackVerdict` for the decision.

## GitHub-hosted Arm64 workflow

Run `verify-production-arm64` manually. It uses
`ubuntu-24.04-arm` and accepts:

- `campaign_mode`: `smoke` or `long`;
- `model_id`: pinned 1.5B by default, with an explicitly smoke-only 0.5B
  option;
- `matrix_shards`: one to three independent receipts;
- `repetitions`: zero for the profile default, otherwise a bounded override;
- `soak_seconds_total`: zero for the profile default, otherwise a bounded
  aggregate measured budget per shard;

The planner rejects hosted-runner requests that remove the timeout headroom.
Each shard pins and verifies the selected manifest model, builds only the
reviewed llama.cpp commit, uploads all raw readiness/replay evidence, and
retains nested plus top-level checksum manifests. It also uploads separate
build provenance containing the complete build/fetch logs, stage-status
records, `CMakeCache.txt`, upstream submodule state, source status, toolchain
versions, and final server/model/manifest hashes. A final job downloads every
expected shard and fails if shard ids are not exactly `1..N`, or if any receipt
is missing, tampered, schema-invalid, provenance-invalid,
boundary-inconsistent, or not `KEEP_CANDIDATE`. On success it uploads a
checksummed machine-readable aggregate receipt binding the exact source,
workflow run/attempt, model, llama.cpp commit, shard ids, shard receipt hashes,
trace digests, duration arithmetic, and claim boundary.

### Remaining same-runner trust boundary

Baseline, temporal candidate, model process, load generator, hash guard, and
receipt writer run as the same OS user on one ephemeral GitHub-hosted VM. The
allowlisted upstream commit prevents arbitrary user-selected upstream code
from executing, and repeated hashes detect on-disk server/model drift, but
there is no process/VM isolation between the measured server and evidence
collector. A compromise in the reviewed executable or same-runner source could
tamper with memory or same-user files before detection. Therefore these are
candidate-only synthetic receipts, not hostile-code sandbox evidence,
independent attestation, or production isolation proof.

## Receipt layout

```text
receipt.json
resolved-config.json
sha256sums.txt
round-001/
  sanitized-traffic.jsonl
  traffic-receipt.json
  baseline/
    arm-receipt.json
    readiness/
      summary.json
      measured-requests.jsonl
      telemetry.csv
      sha256sums.txt
    replay/
      summary.json
      requests.jsonl
      llama-server.log
  candidate/
    ...
```

The top-level `sha256sums.txt` covers every receipt, raw request row, telemetry
sample, nested checksum file, and server log under the campaign directory.
