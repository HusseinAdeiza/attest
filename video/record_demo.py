"""Record the ATTEST demo running in a real terminal, for the submission video.

WHY A REAL TERMINAL AND NOT MOTION GRAPHICS
The product IS a terminal tool plus a decision trail. The evidence is the
decisions themselves and the ledger that records them — including the refusals.
An animated summary would be me describing the product; this is the product.

LESSONS CARRIED OVER FROM THE CAPGRATE VIDEO, EACH OF WHICH COST HOURS
  1. xdotool `type` MANGLES long commands — it dropped underscores and garbled a
     quoted pipeline so badly the take filmed a dead terminal. So the recorder
     types ONE short path with no underscores in it. Everything complex lives
     inside that script.
  2. A REAL Return key event is required, not a newline in the typed string.
  3. RESET THE STATE BEFORE FILMING and assert it is clean. A previous take was
     ruined because state left over from debugging made every step refuse, which
     reads as a broken product rather than a working one.
  4. `ffmpeg -video_size` must NOT carry a colour-depth suffix. "1920x1440x24"
     makes ffmpeg exit instantly with "Unable to parse video_size", producing no
     file at all. Xvfb's -screen DOES take the depth.
  5. Geometry is MEASURED, not guessed. This demo emits 71 lines, longest 203
     chars. 720p fits ~34 lines and would cut the ledger off screen; 1440p at
     13pt fits 86 lines and 266 columns, so the whole run stays visible.
  6. FIND THE WINDOW BY THE NAME IT ACTUALLY HAS. `xdotool search --name bash`
     works because xterm runs bash; searching for the window title finds
     nothing, and searching for "xterm" finds nothing either because that is
     the window CLASS. An earlier version of this file searched for the title
     and aborted with "no window".
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/hackathons/attest")
VIDEO = ROOT / "video"
TAKES = VIDEO / "takes"

FFMPEG = "/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg"
FFPROBE = "/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffprobe"

DISPLAY_NUM = ":94"

# Xvfb's -screen takes the colour depth; ffmpeg's -video_size must not.
SCREEN_GEO = "1920x1440x24"
GRAB_SIZE = "1920x1440"

# 266 columns x 86 rows at 13pt: measured to hold 203-char lines and 71 rows.
GEOMETRY = "266x86"

# The demo is paced to ~37s (see PAUSE_* in run_demo.py). Add the recorder's
# own startup — Xvfb, xterm paint, window focus, the clear, the typed command —
# which measured ~12s, plus several seconds of held final frame so the closing
# line lands before the grab stops.
DEMO_SECONDS = 72

# Typed verbatim. Short, no underscores, no quotes, no shell metacharacters.
CMD = "bash /root/hackathons/attest/video/run.sh"


def sh(*args, **kw):
    return subprocess.run(list(args), **kw)


def main() -> int:
    TAKES.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = TAKES / f"attest_raw_{stamp}.mp4"
    marker = TAKES / "grab_start.txt"

    sh("pkill", "-f", f"Xvfb {DISPLAY_NUM}", capture_output=True)
    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY_NUM, "-screen", "0", SCREEN_GEO, "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    print(f"  Xvfb up on {DISPLAY_NUM} ({SCREEN_GEO})")

    env = dict(os.environ, DISPLAY=DISPLAY_NUM)

    # No -title: the window is located by the process name (bash), which is what
    # xdotool can actually find. A title flag was tried and the search failed.
    term = subprocess.Popen(
        ["xterm", "-display", DISPLAY_NUM, "-geometry", GEOMETRY,
         "-fa", "Monospace", "-fs", "13",
         "-bg", "#0b1020", "-fg", "#e8ecf8",
         "-e", "bash", "--norc", "-i"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3.5)
    print("  terminal up")

    # Locate the window BEFORE recording, so a failure costs no encode time.
    found = sh("xdotool", "search", "--name", "bash", env=env,
               capture_output=True, text=True).stdout.strip().splitlines()
    if not found:
        print("ABORT: no terminal window to drive", file=sys.stderr)
        term.terminate(); xvfb.terminate()
        return 1
    wid = found[-1]
    print(f"  window id {wid}")

    # --- start the grab BEFORE driving the terminal --------------------------
    grab = subprocess.Popen(
        [FFMPEG, "-y", "-f", "x11grab",
         "-video_size", GRAB_SIZE, "-framerate", "24", "-i", DISPLAY_NUM,
         "-t", str(DEMO_SECONDS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Timestamp the moment recording starts so beats can be derived from the
    # take rather than guessed.
    marker.write_text(str(time.time()))
    time.sleep(1.0)

    sh("xdotool", "windowactivate", "--sync", wid, env=env, capture_output=True)
    sh("xdotool", "windowfocus", "--sync", wid, env=env, capture_output=True)
    time.sleep(1.0)

    # Clear the screen so the take opens on an empty terminal, not on a prompt
    # sitting halfway down from a previous run.
    sh("xdotool", "type", "--window", wid, "--delay", "40", "clear", env=env)
    sh("xdotool", "key", "--window", wid, "Return", env=env)
    time.sleep(1.2)

    print(f"  typing: {CMD}")
    sh("xdotool", "type", "--window", wid, "--delay", "55", CMD, env=env)
    time.sleep(0.7)
    sh("xdotool", "key", "--window", wid, "Return", env=env)

    try:
        grab.wait(timeout=DEMO_SECONDS + 40)
    except subprocess.TimeoutExpired:
        grab.kill()
    print("  recording finished")

    term.terminate()
    xvfb.terminate()
    time.sleep(0.8)

    if not out.exists() or out.stat().st_size < 20_000:
        print("FAIL: no usable recording produced", file=sys.stderr)
        return 1

    info = sh(FFPROBE, "-v", "error",
              "-show_entries", "format=duration,size",
              "-show_entries", "stream=width,height,nb_frames",
              "-of", "default=nw=1", str(out),
              capture_output=True, text=True).stdout.strip()
    print(f"\n  wrote {out}")
    print(f"  {out.stat().st_size/1024/1024:.1f} MB")
    print("  " + info.replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
