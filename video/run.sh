#!/usr/bin/env bash
# What the recorder actually runs. Typed as ONE short path so xdotool cannot
# mangle it — see the note in record_demo.py.
#
# BUFFERING RULES, all learned the hard way on the Capgrate video:
#   * `sed` block-buffers when its output is not a TTY  -> not used
#   * `tee` has the same hazard                          -> not used
#   * `grep` block-buffers on non-TTY output             -> stdbuf -oL if used
#   * python's stdout is block-buffered under a pipe     -> python3 -u
# A buffering layer between the demo and the screen makes every step appear at
# once, which destroys the pacing the video depends on.

set -u
cd /root/hackathons/attest || exit 1

PY=.venv/bin/python

echo "ATTEST — recorded demo"
echo "started $(date -u '+%H:%M:%S UTC')"
echo ""

# The demo itself, line-buffered, stamped, and shown live.
$PY -u -m src.demo.run_demo 2>&1 \
  | awk '{ print strftime("%H:%M:%S"), $0; fflush() }' \
  | while IFS= read -r line; do
      printf '%s\n' "${line#* }"
      printf '%s\n' "$line" >> /tmp/attest-demo-timed.txt
    done

echo ""
echo "DEMO-COMPLETE"
