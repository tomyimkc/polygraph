#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 Polygraph contributors
# SPDX-License-Identifier: Apache-2.0
#
# Build the pinned llama-server target into the same cache-backed llama.cpp
# checkout used by build_llamacpp.sh.  It is separate so the normal dispatch
# matrix does not pay for a server binary it never uses.
set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$LIB_DIR/../common.sh"

: "${JOBS:=$(build_jobs_cap)}"
: "${FORCE:=0}"

STAGE="build_llama_server"
STAMP_FILE="$LLAMA_CPP_DIR/.polygraph-server-build-stamp"
STAMP_WANT="ref=$LLAMA_CPP_REF cmake_build_type=Release kleidiai=ON target=llama-server"
BASE_STAMP_FILE="$LLAMA_CPP_DIR/.polygraph-build-stamp"
BASE_STAMP_WANT="ref=$LLAMA_CPP_REF cmake_build_type=Release kleidiai=ON targets=llama-cli,llama-bench"

if [[ "$FORCE" != "1" && -x "$LLAMA_SERVER" && -f "$STAMP_FILE" ]] \
    && [[ "$(cat "$STAMP_FILE" 2>/dev/null)" == "$STAMP_WANT" ]] \
    && [[ -f "$BASE_STAMP_FILE" ]] \
    && [[ "$(cat "$BASE_STAMP_FILE" 2>/dev/null)" == "$BASE_STAMP_WANT" ]]; then
    record_stage "$STAGE" OK "reusing cached server at $LLAMA_SERVER"
    exit 0
fi

if [[ "$FORCE" == "1" || ! -f "$BASE_STAMP_FILE" ]] \
    || [[ "$(cat "$BASE_STAMP_FILE" 2>/dev/null)" != "$BASE_STAMP_WANT" ]]; then
    FORCE="$FORCE" bash "$LIB_DIR/build_llamacpp.sh"
fi

log_info "building --target llama-server with -j$JOBS"
if ! cmake --build "$LLAMA_CPP_DIR/build" --target llama-server -j"$JOBS" \
    >"$LOG_DIR/llamacpp-server-build.log" 2>&1; then
    record_stage "$STAGE" FAIL "server build failed, see $LOG_DIR/llamacpp-server-build.log"
    tail -n 60 "$LOG_DIR/llamacpp-server-build.log" >&2 || true
    exit 1
fi

if [[ ! -x "$LLAMA_SERVER" ]]; then
    record_stage "$STAGE" FAIL "build reported success but $LLAMA_SERVER is missing"
    exit 1
fi

echo "$STAMP_WANT" >"$STAMP_FILE"
record_stage "$STAGE" OK "built $LLAMA_SERVER"
