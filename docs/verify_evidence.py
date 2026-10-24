#!/usr/bin/env python3
"""Verify every checkable claim in docs/evidence.md against the artifacts.

WHY THIS EXISTS
While writing docs/evidence.md I cited a test name from memory that did not
exist, and quoted a zero-crossing figure measured on a different window. Both
were caught only because I stopped to check. A claims document that is not
itself verified is worse than no claims document: it invites a judge to trust a
number nobody has checked.

This script is the check. It reads evidence.md, extracts every test name and
every numeric claim it can mechanically verify, and fails loudly on a mismatch.
Run it before every commit that touches the evidence document.

    python3 docs/verify_evidence.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence.md"
TESTS = ROOT / "tests"

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def test_functions_in_repo() -> set[str]:
    names: set[str] = set()
    for f in TESTS.glob("test_*.py"):
        names |= set(re.findall(r"^def (test_\w+)", f.read_text(), re.M))
    return names


def main() -> int:
    text = EVIDENCE.read_text()
    existing = test_functions_in_repo()
    print(f"evidence.md cites {len(existing)} test functions available; checking named ones\n")

    # ---- 1. every `test_...` token that looks like a FUNCTION must exist ----
    # A token followed by "()" or backticks and containing "_" is a function.
    cited = set(re.findall(r"`(test_[a-z0-9_]+)`", text))
    for name in sorted(cited):
        # Plain file names like test_gate are cited as part of `tests/test_gate.py`
        # and are handled below, so only exact function names are asserted here.
        check(f"test function exists: {name}", name in existing)

    # ---- 2. cited test FILES must exist ----
    for fname in sorted(set(re.findall(r"tests/(test_\w+\.py)", text))):
        check(f"test file exists: {fname}", (TESTS / fname).is_file())

    # ---- 3. the test count in the document must match a real run ----
    m = re.search(r"(\d+)\s+passed", text)
    if m:
        claimed = int(m.group(1))
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q",
                            "--no-header"], cwd=ROOT,
                           capture_output=True, text=True)
        got = re.search(r"(\d+) passed", r.stdout)
        actual = int(got.group(1)) if got else -1
        check(f"test count claim: {claimed}", actual == claimed,
              f"actual={actual}")

    # ---- 4. per-file test counts in the table must match reality ----
    for fname, claimed in re.findall(r"tests/(test_\w+\.py)`\s*\|\s*(\d+)\s*\|", text):
        r = subprocess.run([sys.executable, "-m", "pytest", f"tests/{fname}",
                            "-q", "--no-header", "--collect-only"],
                           cwd=ROOT, capture_output=True, text=True)
        # pytest --collect-only prints node ids AND a summary line. Counting
        # "::test_" matches both, which inflates the total by one. Count only
        # lines that look like a node id:  tests/x.py::test_name
        actual = len(re.findall(r"^tests/\S+::test_\w+(\[.*\])?$", r.stdout, re.M))
        # re.findall yields STRINGS, so `claimed` arrives as "14" while actual is
        # the int 14. Comparing them directly is always False, which made this
        # checker report four failures that did not exist. Coerce explicitly.
        check(f"{fname} claims {claimed} tests", actual == int(claimed),
              f"actual={actual}")

    # ---- 5. the demo's ledger totals must match a real run ----
    env = dict(os.environ, ATTEST_PACE="0")   # keep the venv's environment
    r = subprocess.run([sys.executable, "-m", "src.demo.run_demo"], cwd=ROOT,
                       capture_output=True, text=True, env=env)
    out = r.stdout
    for figure in ("15000.00", "12000.00", "9000.00"):
        check(f"demo output contains {figure}", figure in out)
    for decision in ("AUTHORISE", "AUTHORISE_CAPPED", "ESCALATE", "REFUSE"):
        check(f"demo output contains decision {decision}", decision in out)

    # ---- 6. the video file named in the document must exist ----
    for rel in re.findall(r"`(video/[\w./-]+\.mp4)`", text):
        check(f"video exists: {rel}", (ROOT / rel).is_file())

    # ---- 7. no credential-shaped string may appear in the document ----
    # The document claims the repo is clean; it must not be the leak itself.
    suspicious = re.findall(r"[A-Fa-f0-9]{64,}", text)
    check("no long hex string (possible key) in evidence.md", not suspicious,
          f"{len(suspicious)} found" if suspicious else "")

    print(f"\n{checks} checks, {len(failures)} failed")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
