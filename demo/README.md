<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Polygraph contributors -->

# Contest video production

The **single authoritative submission cut** is the reproducible,
repository-native hybrid render. It combines sourced evidence cards with an
auditable 20-second playback derived from a real local `make demo` run. It does
not depend on the external presenter-generation workspace, and it does not
invent terminal output or evidence values.

```bash
python3 -m pip install -r demo/contest-video/requirements.txt
python3 tools/render_contest_video.py --self-test
python3 tools/render_contest_video.py --dry-run
python3 tools/render_contest_video.py --capture-demo
```

`--capture-demo` is the authoritative production command: it captures from an
isolated `git archive HEAD` using a strict allowlisted environment, validates
the new capture receipt, and renders the MP4. Once that receipt exists and
still matches the current branch, HEAD, capture driver, host command paths,
and hashes, a reproducible cached-capture rebuild is:

```bash
python3 tools/render_contest_video.py
```

Default artifacts:

```text
demo/out/polygraph-contest-final.mp4
demo/out/polygraph-contest-final.srt
demo/out/polygraph-contest-final.sha256
demo/out/polygraph-contest-final.validation.json
demo/out/qa/contact-sheet.jpg
```

Final post-hardening sidecar values: **169.021333 seconds**, **9,239,731
bytes**, SHA-256
**`0deb7f67697b45ac8b9b38b518d87240ac556db8bb82107f23158862372f8f30`**.
The validation sidecar remains the authoritative machine-readable source.

The renderer fails before encoding if the required evidence has drifted, if the
story order changes, or if its declared timeline is not under 180 seconds. After
encoding it uses `ffprobe` to require:

- duration strictly below 180 seconds;
- 1920×1080 H.264 at 30 fps;
- web-compatible 4:2:0 pixel format;
- AAC, 48 kHz, stereo audio;
- a fast-start MP4 (`moov` before `mdat`).

The final validation sidecar is authoritative for duration, byte size, codec
properties, and SHA-256. The generated block in
[`docs/VIDEO-PRODUCTION.md`](../docs/VIDEO-PRODUCTION.md) is rewritten directly
from that sidecar after every successful final render.

On macOS, the default `--tts auto` uses the built-in `say` command. Elsewhere it
renders the same fixed timeline with a silent AAC track:

```bash
python3 tools/render_contest_video.py --tts none
```

The exact six-beat script, on-screen cards, source footers, and fixed durations
live in [`contest-video/story.json`](contest-video/story.json). The judge-facing
shot list is [`SHOTLIST.md`](SHOTLIST.md).

## Real project-function footage

Beat 4 keeps the original 29-second story budget but replaces 20 seconds of the
static card:

- 9 seconds: sourced technical-fixes/tooling card;
- 20 seconds: literal output from an actual `make demo` PTY run.

Playback timing is normalized by revealing captured lines in order with
legible pauses and holding the final captured frame. Ephemeral capture-root
paths are replaced with `$CAPTURE_ROOT`, PTY line endings are normalized to LF,
and semantic CLI output ordering is unchanged. The
auditable bundle is:

```text
demo/contest-video/live-run/make-demo.ansi
demo/contest-video/live-run/make-demo.txt
demo/contest-video/live-run/make-demo.events.jsonl
demo/contest-video/live-run/make-demo.gif
demo/contest-video/live-run/receipt.json
demo/contest-video/live-run/SHA256SUMS
```

The receipt binds the reviewed branch and HEAD archive, capture-driver hash,
strict environment key allowlist, exact executable paths, command, overall and
liar/honest exit codes, wall time, PTY event count, output hashes, and playback
duration. The renderer reconstructs the normalized ANSI transcript from the event stream
and rejects absolute/traversal/symlink paths, unknown receipt keys or capture
filenames, stale source state, changed hashes, oversize output, or a failed
capture.

## Story order

The production order is contractual:

1. baseline/banner claim;
2. L3 executed-kernel proof;
3. measured cost;
4. technical fixes and reusable tooling;
5. automated Arm64 readiness run `31294460364`;
6. honest boundary.

The RTX PRO 6000 30B-A3B campaign appears only in the final boundary beat as
**non-Arm systems evidence**. It is never presented as Arm contest evidence or
as validation of KleidiAI dispatch.

## Evidence lock

`tools/render_contest_video.py` reads and cross-checks:

- `results/server/spark-provenance.txt`;
- `results/scale/scale-experiment.json`;
- `results/upstream/llamacpp-26334-cuda-host-buffer.json`;
- `results/production-readiness/arm64-31294460364/{summary,validation-receipt,workflow-receipt}.json`;
- `results/production-readiness/pro6000-31289517517/aggregate.json`;
- `tools/polygraph` and `tools/verify_dispatch.py`.

The validation receipt records SHA-256 hashes for those exact inputs.

## What happened to the older terminal demo?

`demo/demo.sh` remains a useful interactive investigation walkthrough and
rehearsal, but it is not the current final video pipeline. Its 2026-08-04 arc
predates the automated Arm64 readiness campaign and cannot satisfy the current
contest story without a recut.

Any earlier 90-second or 103-second presenter/export cut is historical only and
must not be submitted as current. The preserved 90-second Remotion render is
audited in
[`docs/VIDEO-PRODUCTION.md`](../docs/VIDEO-PRODUCTION.md). It remains historical
provenance, not the current submission cut.

## Independent terminal authenticity check

Before publishing, a reviewer can still run the project live:

```bash
make demo
tools/polygraph list
tools/polygraph explain llama-cpp-kleidiai
```

Those commands are supporting reproducibility. The authoritative video already
contains the receipt-bound `make demo` playback described above.
