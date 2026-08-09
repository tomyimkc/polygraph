<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Polygraph contributors -->

# Contest video

The **single authoritative submission cut** is the repository-native hybrid
render produced from
[`demo/contest-video/story.json`](../demo/contest-video/story.json) by
[`tools/render_contest_video.py`](../tools/render_contest_video.py). The
maintained under-three-minute plan is
[`demo/SHOTLIST.md`](../demo/SHOTLIST.md). Its machine-readable source is
the story JSON above.

Create a fresh, audited project-function capture and render the final cut:

```bash
python3 -m pip install -r demo/contest-video/requirements.txt
python3 tools/render_contest_video.py --self-test
python3 tools/render_contest_video.py --dry-run
python3 tools/render_contest_video.py --capture-demo
```

After that fresh capture has passed receipt validation, the same final cut can
be rebuilt from the cached capture:

```bash
python3 tools/render_contest_video.py
```

Authoritative local artifacts:

```text
demo/out/polygraph-contest-final.mp4
demo/out/polygraph-contest-final.srt
demo/out/polygraph-contest-final.validation.json
demo/out/polygraph-contest-final.sha256
demo/out/qa/contact-sheet.jpg
```

Final post-hardening sidecar values: **169.021333 seconds**, **9,239,731
bytes**, SHA-256
**`0deb7f67697b45ac8b9b38b518d87240ac556db8bb82107f23158862372f8f30`**.
The validation sidecar remains the authoritative machine-readable source.

The renderer loads all displayed figures from repository evidence, writes an
authored sidecar SRT, and emits a validation receipt with source hashes. Beat 4
uses 9 seconds of its evidence card followed by 20 seconds of footage derived
from an actual local `make demo` PTY run. Literal output lines are unchanged;
only line-reveal pauses and the final-frame hold are normalized for legibility.
The raw ANSI transcript, clean transcript, timestamped/base64 event stream,
playback GIF, receipt, and `SHA256SUMS` remain under
`demo/contest-video/live-run/`.

The renderer fails unless the capture matches the current reviewed branch,
commit archive, capture-driver hash, strict environment policy, and receipt
hashes. It also fails unless the final MP4 is under 180 seconds and uses
web-friendly H.264/AAC, 1080p, 30 fps, 4:2:0, and fast-start settings. Exact
post-render duration, byte size, SHA-256, and codec values are generated from
the final validation sidecar into
[`VIDEO-PRODUCTION.md`](VIDEO-PRODUCTION.md).

## Required story

1. baseline/banner claim;
2. L3 executed-kernel proof;
3. measured cost;
4. technical fixes and reusable tooling;
5. automated Arm64 readiness run `31294460364`;
6. honest boundary.

The RTX PRO 6000 30B-A3B control may appear only as non-Arm systems evidence.
It does not validate Arm or KleidiAI dispatch.

Any previous 90-second or 103-second presenter/export cut is historical only
and must not be submitted as the current cut. The preserved 90-second
pipeline audit and the reasons it no longer matches the submission story are
in [`VIDEO-PRODUCTION.md`](VIDEO-PRODUCTION.md).
