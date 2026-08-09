<!-- SPDX-License-Identifier: Apache-2.0 -->

# Arm64 same-artifact temporal-control campaign

This directory preserves the concise, independently revalidated receipts for:

- smoke preflight run [`31312308726`](https://github.com/tomyimkc/polygraph/actions/runs/31312308726);
- long campaign run [`31312723300`](https://github.com/tomyimkc/polygraph/actions/runs/31312723300); and
- exact campaign source commit
  [`5833ff20f503d126a8654f127419ed1e27ce2f5d`](https://github.com/tomyimkc/polygraph/commit/5833ff20f503d126a8654f127419ed1e27ce2f5d).

The original GitHub artifact ZIPs, build-provenance bundles, external validator, campaign-source
archive, and contest video are preserved in the immutable
[`arm-create-evidence-31312723300`](https://github.com/tomyimkc/polygraph/releases/tag/arm-create-evidence-31312723300)
release.

## Claim boundary

```text
actualProductionTraffic:false
productionReady:false
candidateOnly:true
```

This is same-binary, same-model temporal-control evidence. The labels `reviewed-baseline` and
`reviewed-temporal-candidate` used identical `llama-server` and model hashes, so the campaign does
**not** establish a candidate optimization or uplift.

The campaign also does not establish production traffic, deployment authorization, continuous
availability, host/network failover, tenant isolation, or application-specific correctness.
Controlled process restarts, model reloads, and deterministic load-generator delay/error
injections exercise the synthetic gate and rollback-decision path only.

## Verified observations

| profile | shards | measured requests | measured failures | aggregate measured duration | longest continuous segment | result |
|---|---:|---:|---:|---:|---:|---|
| smoke | 1 | 38 | 0 | 8 seconds | 4 seconds | `PASS` / `KEEP_CANDIDATE` |
| long | 2 | 79,684 | 0 | 36,000 seconds (**10 aggregate hours**) | 9,000 seconds (**2.5 hours**) | both shards `PASS` / `KEEP_CANDIDATE` |

The 36,000-second figure sums four independent 9,000-second baseline/candidate segments across
two parallel shards. It is **10 aggregate measured hours**, not 10 continuous hours. The longest
continuous segment is 9,000 seconds, or 2.5 hours.

Each shard:

- used pinned Qwen2.5 1.5B Q4_0 model SHA-256
  `dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb`;
- built reviewed `llama.cpp` commit
  `dbadb68eecdfb3ab0e86872d011738fc937f0364`;
- retained identical baseline/candidate deterministic trace digests;
- recorded zero measured failures, zero unexpected replay errors, and zero foreign synthetic
  sentinel leaks; and
- kept `deploymentAuthorized:false` and `tenantIsolationClaimed:false`.

## Files

| file | role |
|---|---|
| `smoke-aggregate-receipt.json` | workflow-produced smoke aggregate |
| `smoke-validation-receipt.json` | independent smoke artifact/provenance validation |
| `long-aggregate-receipt.json` | workflow-produced two-shard aggregate |
| `long-shard-1-receipt.json` | complete strict-verifier shard 1 receipt |
| `long-shard-2-receipt.json` | complete strict-verifier shard 2 receipt |
| `long-validation-receipt.json` | independent long-run artifact/provenance validation and totals |
| `workflow-run-*.json` | GitHub Actions run metadata captured during validation |
| `workflow-artifacts-*.json` | exact artifact sets and GitHub-advertised digests |
| `package-sha256sums.txt` | checksum manifest for this committed package |

The external validation re-downloaded the original ZIP bytes from GitHub, matched the API SHA-256
digest and byte size, CRC-tested and path-checked each ZIP, reran
`tools/production_campaign.py verify`, checked exact source/model/toolchain provenance, recomputed
the aggregate from the strict shard receipts, and required the exact artifact set.

## Verify

```bash
cd results/production-readiness/arm64-campaign-31312308726-31312723300
sha256sum -c package-sha256sums.txt

jq -e '
  .actualProductionTraffic == false and
  .productionReady == false and
  .candidateOnly == true and
  .aggregateMeasuredSoakSeconds == 36000 and
  .longestContinuousSoakSegmentSeconds == 9000 and
  ([.shards[].gateVerdict] | all(. == "PASS")) and
  ([.shards[].rollbackVerdict] | all(. == "KEEP_CANDIDATE")) and
  ([.shards[].sameArtifactControl] | all)
' long-aggregate-receipt.json

jq -e '
  .checks.workflowAllChecksPassed == true and
  .checks.exactArtifactSet == true and
  .checks.originalGithubZipDigests == true and
  .checks.strictShardReceiptVerification == true and
  .checks.aggregateRecomputedFromShards == true and
  .checks.sameArtifactTemporalControl == true and
  .durations.aggregateMeasuredSoakSeconds == 36000 and
  .durations.longestContinuousSoakSegmentSeconds == 9000 and
  .totals.combinedMeasuredRequests == 79684 and
  .totals.measuredFailures == 0
' long-validation-receipt.json
```
