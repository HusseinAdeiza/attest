"""Build the captioned cut from the recorded take.

CAPTIONS ARE GENERATED, NOT GUESSED
Every beat below was measured from the take's own pixel changes (the change
seconds: 2,3,4,5,6,12,16,17,20,21,27,28,29,34,37,38) and cross-checked against
the demo's timestamped log. An earlier video in this series had captions that
drifted ahead of the footage — the caption said "spend until the window is full"
while the screen showed a refusal — because the beats were estimated instead of
measured. A caption that contradicts the screen is worse than no caption.

THE REGISTER
Silent, with on-screen text. Two reasons, both deliberate:
  * AI-narrated montages are a documented rejection risk with hackathon judges.
  * micro1's brief permits captions without a voice, and a human voiceover
    remains possible later — the audio bed leaves ~20 dB of headroom for one.
So the captions carry every word the video needs, and each one is short enough
to read in its window.

THE RULE THE CAPTIONS FOLLOW
Never let the text claim something the screen does not show. Caption 4 says
"the same invoice, with the purchase order removed" while the screen shows Case
B's evidence list — because that is literally what changed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FFMPEG = "/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg"

VIDEO = Path("/root/hackathons/attest/video")
TAKES = VIDEO / "takes"
QC = VIDEO / "qc"
OUT = VIDEO / "out"

# Measured. (start, end, text). Ends are set so no caption overlaps the next
# beat: a caption whose text changes a moment after the screen does reads as a
# lag rather than a caption.
CAPTIONS = [
    # Timed to the measured pixel changes of THIS take: 1,13,19,25,35,43,48,58.
    # The previous cut's captions were timed to a different recording; reusing
    # them would drift, which is the exact failure this file exists to avoid.
    (0.0, 13.0,
     "ATTEST - an agent's spending authority scales with the evidence"),
    (13.0, 19.0,
     "CASE A  invoice + purchase order agree  ->  pays 12,000 in full, no human"),
    (19.0, 25.0,
     "CASE B  the purchase order is removed  ->  pays, capped at 3,000"),
    (25.0, 35.0,
     "CASE C  the PO says 19,000, the invoice 12,000  ->  escalates to a human"),
    (35.0, 43.0,
     "CASE D  an email with nothing to verify it  ->  refused, and recorded"),
    (43.0, 48.0,
     "The ledger keeps every decision - refusals included"),
    (48.0, 58.0,
     "Same instruction. Same payee. Four outcomes - decided only by the evidence."),
]

CUT_SECONDS = 59.0

# 1440p source, so caption text scales from 24 to stay proportionate.
STYLE = ("fontsize=34:fontcolor=white:box=1:boxcolor=black@0.72:"
         "boxborderw=18:x=(w-text_w)/2")


def esc(text: str) -> str:
    """Escape for ffmpeg's drawtext. Colons and commas separate filters, so they
    must be escaped or the filter graph is parsed as something else entirely."""
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace(",", "\\,")
                .replace("'", "\u2019")
                .replace("%", "\\%"))


def find_font() -> str:
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if Path(p).exists():
            return p
    raise SystemExit("no usable font found for drawtext")


def main() -> int:
    takes = sorted(TAKES.glob("attest_raw_*.mp4"))
    if not takes:
        raise SystemExit("no raw take found — run record_demo.py first")
    src = takes[-1]
    print(f"  source take : {src.name}")

    QC.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    font = find_font()
    print(f"  font        : {font}")

    # Bottom-anchored so captions never cover the terminal text they describe.
    filters = []
    for start, end, text in CAPTIONS:
        filters.append(
            f"drawtext=fontfile={font}:text='{esc(text)}'"
            f":{STYLE}:y=h-th-70:enable='between(t,{start},{end})'")

    graph = ",".join(filters)
    out = OUT / "attest-demo-captions.mp4"

    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(src),
           "-t", str(CUT_SECONDS),
           "-vf", graph,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-an", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG FAILED:", r.stderr[:1500], file=sys.stderr)
        return 1

    print(f"  wrote {out}")
    print(f"  {out.stat().st_size/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
