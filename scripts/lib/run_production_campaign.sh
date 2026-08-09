#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
#
# Thin, fail-closed wrapper for tools/production_campaign.py.
#
# Required:
#   BASELINE_SERVER, BASELINE_MODEL, PRODUCTION_OUT
#
# Optional:
#   CANDIDATE_SERVER       defaults to BASELINE_SERVER
#   CANDIDATE_MODEL        defaults to BASELINE_MODEL
#   PRODUCTION_PROFILE     smoke (default) or long
#   PRODUCTION_REPETITIONS positive integer override
#   PRODUCTION_SOAK_SECONDS_TOTAL positive measured-seconds budget override
#   PRODUCTION_SHARD_ID    positive matrix shard identifier (default 1)
#   PRODUCTION_CONFIG      config path
#   PRODUCTION_MODEL_ID    manifest id (default qwen2.5-1.5b-q4_0)
#   PRODUCTION_MODEL_MANIFEST model manifest path
#   PRODUCTION_LLAMA_CPP_SHA must equal the reviewed allowlisted commit
#   PRODUCTION_RESOLVED_LLAMA_CPP_SHA resolved build checkout commit
#   PRODUCTION_HOSTED_WORKFLOW set to 1 only in the main-branch hosted lane
#   PRODUCTION_HOST / PRODUCTION_PORT
#   BASELINE_LABEL / CANDIDATE_LABEL
set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$LIB_DIR/../.." && pwd)"

: "${PRODUCTION_CONFIG:=$REPO_ROOT/configs/production-readiness.json}"
: "${PRODUCTION_MODEL_MANIFEST:=$REPO_ROOT/scripts/models.txt}"
: "${PRODUCTION_MODEL_ID:=qwen2.5-1.5b-q4_0}"
: "${PRODUCTION_LLAMA_CPP_SHA:=dbadb68eecdfb3ab0e86872d011738fc937f0364}"
: "${PRODUCTION_RESOLVED_LLAMA_CPP_SHA:=$PRODUCTION_LLAMA_CPP_SHA}"
: "${PRODUCTION_HOSTED_WORKFLOW:=0}"
: "${PRODUCTION_SOURCE_REPOSITORY:=local}"
: "${PRODUCTION_SOURCE_REF:=local}"
: "${PRODUCTION_SOURCE_SHA:=local}"
: "${PRODUCTION_PROFILE:=smoke}"
: "${PRODUCTION_SHARD_ID:=1}"
: "${PRODUCTION_HOST:=127.0.0.1}"
: "${PRODUCTION_PORT:=18081}"
: "${BASELINE_LABEL:=baseline}"
: "${CANDIDATE_LABEL:=candidate}"

: "${BASELINE_SERVER:?BASELINE_SERVER is required}"
: "${BASELINE_MODEL:?BASELINE_MODEL is required}"
: "${PRODUCTION_OUT:?PRODUCTION_OUT is required}"
: "${CANDIDATE_SERVER:=$BASELINE_SERVER}"
: "${CANDIDATE_MODEL:=$BASELINE_MODEL}"

fail() {
    printf 'production campaign: %s\n' "$*" >&2
    exit 2
}

[[ -f "$PRODUCTION_CONFIG" ]] || fail "config not found: $PRODUCTION_CONFIG"
[[ -f "$PRODUCTION_MODEL_MANIFEST" ]] \
    || fail "model manifest not found: $PRODUCTION_MODEL_MANIFEST"
[[ -x "$BASELINE_SERVER" ]] || fail "baseline server is not executable: $BASELINE_SERVER"
[[ -f "$BASELINE_MODEL" ]] || fail "baseline model not found: $BASELINE_MODEL"
[[ -x "$CANDIDATE_SERVER" ]] || fail "candidate server is not executable: $CANDIDATE_SERVER"
[[ -f "$CANDIDATE_MODEL" ]] || fail "candidate model not found: $CANDIDATE_MODEL"
[[ "$PRODUCTION_SHARD_ID" =~ ^[1-9][0-9]*$ ]] \
    || fail "PRODUCTION_SHARD_ID must be a positive integer"
[[ "$PRODUCTION_PORT" =~ ^[0-9]+$ ]] \
    || fail "PRODUCTION_PORT must be an integer"
(( 10#$PRODUCTION_PORT >= 1 && 10#$PRODUCTION_PORT <= 65535 )) \
    || fail "PRODUCTION_PORT must be between 1 and 65535"
[[ "$PRODUCTION_LLAMA_CPP_SHA" == "dbadb68eecdfb3ab0e86872d011738fc937f0364" ]] \
    || fail "PRODUCTION_LLAMA_CPP_SHA is not the reviewed allowlisted commit"
[[ "$PRODUCTION_RESOLVED_LLAMA_CPP_SHA" == "$PRODUCTION_LLAMA_CPP_SHA" ]] \
    || fail "resolved llama.cpp SHA does not match the reviewed commit"
[[ "$PRODUCTION_HOSTED_WORKFLOW" == "0" || "$PRODUCTION_HOSTED_WORKFLOW" == "1" ]] \
    || fail "PRODUCTION_HOSTED_WORKFLOW must be 0 or 1"

command=(
    python3 "$REPO_ROOT/tools/production_campaign.py" run
    --config "$PRODUCTION_CONFIG"
    --profile "$PRODUCTION_PROFILE"
    --baseline-server "$BASELINE_SERVER"
    --baseline-model "$BASELINE_MODEL"
    --candidate-server "$CANDIDATE_SERVER"
    --candidate-model "$CANDIDATE_MODEL"
    --model-id "$PRODUCTION_MODEL_ID"
    --model-manifest "$PRODUCTION_MODEL_MANIFEST"
    --llama-cpp-sha "$PRODUCTION_LLAMA_CPP_SHA"
    --resolved-llama-cpp-sha "$PRODUCTION_RESOLVED_LLAMA_CPP_SHA"
    --source-repository "$PRODUCTION_SOURCE_REPOSITORY"
    --source-ref "$PRODUCTION_SOURCE_REF"
    --source-sha "$PRODUCTION_SOURCE_SHA"
    --baseline-label "$BASELINE_LABEL"
    --candidate-label "$CANDIDATE_LABEL"
    --out-dir "$PRODUCTION_OUT"
    --host "$PRODUCTION_HOST"
    --port "$PRODUCTION_PORT"
    --shard-id "$PRODUCTION_SHARD_ID"
)

if [[ "$PRODUCTION_HOSTED_WORKFLOW" == "1" ]]; then
    command+=(--hosted-workflow)
fi

if [[ -n "${PRODUCTION_REPETITIONS:-}" ]]; then
    [[ "$PRODUCTION_REPETITIONS" =~ ^[1-9][0-9]*$ ]] \
        || fail "PRODUCTION_REPETITIONS must be a positive integer"
    command+=(--repetitions "$PRODUCTION_REPETITIONS")
fi

if [[ -n "${PRODUCTION_SOAK_SECONDS_TOTAL:-}" ]]; then
    [[ "$PRODUCTION_SOAK_SECONDS_TOTAL" =~ ^[0-9]+([.][0-9]+)?$ ]] \
        || fail "PRODUCTION_SOAK_SECONDS_TOTAL must be positive numeric seconds"
    python3 - "$PRODUCTION_SOAK_SECONDS_TOTAL" <<'PY' \
        || fail "PRODUCTION_SOAK_SECONDS_TOTAL must be greater than zero"
import sys
raise SystemExit(0 if float(sys.argv[1]) > 0 else 1)
PY
    command+=(--soak-seconds-total "$PRODUCTION_SOAK_SECONDS_TOTAL")
fi

printf 'production campaign profile=%s shard=%s out=%s\n' \
    "$PRODUCTION_PROFILE" "$PRODUCTION_SHARD_ID" "$PRODUCTION_OUT" >&2
exec "${command[@]}"
