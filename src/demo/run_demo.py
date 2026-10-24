"""The demo: ONE invoice, FOUR evidence profiles, FOUR different outcomes.

THE POINT OF THE WHOLE PROJECT IN ONE RUN
The instruction never changes — 12,000 USD to Meridian Freight, reference
INV-4471. Only the evidence behind it changes. If the four outcomes were the
same, this product would be indistinguishable from a static spending limit.

WHY THE PAYING PATHS ARE FIRST
"Refuses when the evidence is weak" is the easy half and it reads as a gotcha —
an agent that mostly says no. The product's value is the other half: strong
evidence pays WITHOUT a human, at full value, and that is what a finance team
is buying. So the run leads with the autonomous payment.

WHAT IS REAL IN THIS RUN
  - the evidence documents are files on disk, read and parsed
  - the extraction, scoring and gating are the shipped code, not a demo shim
  - the rail is the mock, LABELLED as the mock in the output
Nothing here is seeded state or a canned decision: change a source in
`evidence_samples/` and the outcome changes with it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.analyst.extract import Extraction, extract
from src.evidence.score import Decision, Instruction, Origin, Source, assess
from src.gate.execute import Ledger, execute
from src.rails.payment import build_rail

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "evidence_samples"

# Pacing, for the recorded demo.
#
# THE DEMO PRINTS ALL 71 LINES IN 0.07 SECONDS. That is fine as a CLI and
# useless as a video: the audience sees the four cases arrive as one flash and
# then a static screen. Measured with a subprocess reading stdout line by line —
# the first and last lines both arrived at 0.07–0.08s.
#
# WHY THESE VALUES AND NOT SMALLER ONES
# A first pass used 2.2s per case and the whole run took 11 seconds of content.
# Case A alone prints eleven lines — the instruction, six evidence facts, the
# decision, the amount, and the reasoning. Eleven lines in 2.2 seconds is about
# 200ms a line, which is below reading speed and well below reading speed for
# numbers. The case that decides the video's argument was the case nobody could
# finish reading.
#
# So the pause is sized to the CONTENT of each case, not a flat number:
# ~5s for the corroborated case (the most lines), ~4s for the rest, and a longer
# hold on the ledger because the totals are the punchline.
PAUSE_BETWEEN_CASES = 6.0
PAUSE_AFTER_BIG_CASE = 6.5     # Case A prints the most
PAUSE_BEFORE_LEDGER = 4.0
PAUSE_AFTER_LEDGER = 8.0       # the totals and closing line need a beat
PACED = os.environ.get("ATTEST_PACE", "1") != "0"


def beat(seconds: float) -> None:
    """Sleep, but only when pacing is on.

    Gated by an environment variable so the test suite and any scripted use run
    at full speed. A demo whose only mode is slow is a demo nobody re-runs.
    """
    if PACED:
        time.sleep(seconds)
        sys.stdout.flush()

# The instruction is identical in all four profiles. That is the demonstration.
INSTRUCTION = Instruction(
    amount=Decimal("12000.00"),
    currency="USD",
    payee_name="Meridian Freight Ltd",
    payee_account="GB29NWBK60161331926819",
    reference="INV-4471",
)


@dataclass
class Profile:
    key: str
    title: str
    what_changed: str            # one line a judge can verify on screen
    documents: list[tuple[str, Origin]]   # (filename, origin)


PROFILES = [
    Profile(
        key="A",
        title="CORROBORATED — pays in full, no human",
        what_changed="the invoice plus the purchase order that authorised it",
        documents=[("invoice_normal.txt", Origin.INVOICE_PDF),
                   ("purchase_order.txt", Origin.PURCHASE_ORDER)],
    ),
    Profile(
        key="B",
        title="SINGLE SOURCE — pays, but capped at a quarter",
        what_changed="the same invoice, with the purchase order removed",
        documents=[("invoice_normal.txt", Origin.INVOICE_PDF)],
    ),
    Profile(
        key="C",
        title="CONTRADICTED — a human must decide",
        what_changed="a purchase order stating a different total",
        documents=[("invoice_normal.txt", Origin.INVOICE_PDF),
                   ("purchase_order_mismatch.txt", Origin.PURCHASE_ORDER)],
    ),
    Profile(
        key="D",
        title="UNVERIFIABLE — refused, and recorded as refused",
        what_changed="an email asking for payment, with no supporting document",
        documents=[("email_unverified.txt", Origin.MESSAGE)],
    ),
]

# An unverifiable source is declared, not inferred: an anonymous instruction
# has no accountable origin, which is a property of the document's provenance,
# not of its text. A judge can see this declaration on screen.
UNVERIFIABLE = {"email_unverified.txt"}

RULE = "─" * 74


def load(name: str) -> str:
    p = SAMPLES / name
    if not p.exists():
        raise SystemExit(f"missing sample evidence: {p}")
    return p.read_text()


def build_sources(profile: Profile, now: datetime) -> list[Source]:
    sources = []
    for filename, origin in profile.documents:
        text = load(filename)
        ex = extract(text, origin, label=filename)
        # Freshness is a property of when the document was ISSUED, which here is
        # "recently" for every sample. The staleness path is covered by tests
        # and would be exercised by backdating a sample.
        ex.observed_at = now - timedelta(days=2)
        sources.append(ex.to_source(
            declared_verifiable=filename not in UNVERIFIABLE))
    return sources


def render_evidence(sources) -> list[str]:
    """The evidence, one line per fact — minus the facts the header already states.

    WHY THIS PRUNES RATHER THAN PRINTS EVERYTHING
    The first version printed all three facts for all nine sources: 18 lines.
    But `payee_account` is GB29NWBK60161331926819 in every case — one distinct
    value across all nine — and it is already on screen in the instruction block
    four lines above. The invoice and email references are likewise identical in
    every case where they appear.

    Repeating them costs ~12 of 76 lines, and the terminal fits 58 (measured:
    1440px at a 24.6px line pitch). The overflow pushed the ledger's last two
    rows, the totals, and the closing line off screen — the parts of the video
    that carry the argument.

    So: a fact is shown when it DIFFERS from the header or VARIES between
    sources. Those are the ones that decide the outcome. Amounts are always
    shown, because the amount is the whole subject.
    """
    # Facts already displayed in the instruction block. Case-insensitive on the
    # value, since documents format them differently ("USD 12,000.00" vs bare).
    header_values = {
        str(INSTRUCTION.payee_account),
        str(INSTRUCTION.reference),
    }
    lines: list[str] = []
    for s in sources:
        shown = []
        for f in s.facts:
            value = str(f.value)
            if f.key in ("payee_account", "reference"):
                bare = value.replace("USD", "").strip()
                if value in header_values or bare in header_values:
                    continue
            shown.append(f)
        if not shown:
            continue
        label = s.label or s.origin.value
        for f in shown:
            lines.append(f"      {label:20s} {f.key:15s} = {f.value}")
    return lines


def main() -> int:
    now = datetime.now(timezone.utc)
    rail, rail_label = build_rail(prefer_live=True)
    ledger = Ledger()

    print("ATTEST — the agent's spending authority scales with the evidence")
    print(f"rail: {rail_label}"
          + ("   (live payments are NOT verified — see README)"
             if rail_label == "mock" else ""))
    print(RULE)
    print(f"instruction (IDENTICAL in all four cases):")
    print(f"  pay {INSTRUCTION.amount} {INSTRUCTION.currency} "
          f"to {INSTRUCTION.payee_name}")
    print(f"  account {INSTRUCTION.payee_account}   reference {INSTRUCTION.reference}")
    print(RULE)

    for profile in PROFILES:
        sources = build_sources(profile, now)
        result = assess(INSTRUCTION, sources, now=now)

        print()
        print(f"CASE {profile.key} — {profile.title}")
        print(f"  what changed : {profile.what_changed}")
        print(f"  evidence read: " + ", ".join(
            f"{s.label or s.origin.value}"
            + ("" if s.verifiable else " (UNVERIFIABLE)")
            for s in sources))
        for line in render_evidence(sources):
            print(line)

        out = execute(result, instruction_amount=INSTRUCTION.amount,
                      currency=INSTRUCTION.currency,
                      payee_name=INSTRUCTION.payee_name,
                      payee_account=INSTRUCTION.payee_account,
                      reference=INSTRUCTION.reference,
                      rail=rail, ledger=ledger)

        print(f"  -> {out.decision.value}  ({out.reason.value})")
        if out.paid:
            print(f"  -> PAID {out.paid} {INSTRUCTION.currency}   ref {out.reference}")
        elif out.decision is Decision.ESCALATE:
            print(f"  -> no money moved; sent for human approval ({out.reference})")
        else:
            print(f"  -> no money moved")
        for d in result.detail:
            print(f"     · {d}")

        # Let the case land before the next one arrives. Case A gets a longer
        # hold because it prints the most lines.
        beat(PAUSE_AFTER_BIG_CASE if profile.key == "A" else PAUSE_BETWEEN_CASES)

    beat(PAUSE_BEFORE_LEDGER)
    print()
    print(RULE)
    print("LEDGER — every decision, including the refusals")
    print(RULE)
    print(ledger.render())

    t = ledger.totals()
    print(RULE)
    print(f"  paid             {t['paid']} {INSTRUCTION.currency}")
    print(f"  refused          {t['refused']} {INSTRUCTION.currency} "
          f"(no payment attempted)")
    print(f"  escalated        {t['escalated']} {INSTRUCTION.currency} "
          f"(awaiting a human)")
    print(f"  withheld by cap  {t['withheld_by_cap']} {INSTRUCTION.currency} "
          f"(paid in part because evidence was thin)")
    print(RULE)
    print()
    print("The instruction never changed. The evidence did.")
    beat(PAUSE_AFTER_LEDGER)

    # Machine-readable companion, so the video and the README quote the run
    # rather than restating it from memory.
    out_path = ROOT / "demo_last_run.json"
    out_path.write_text(json.dumps({
        "rail": rail_label,
        "instruction": {
            "amount": str(INSTRUCTION.amount),
            "currency": INSTRUCTION.currency,
            "payee": INSTRUCTION.payee_name,
            "reference": INSTRUCTION.reference,
        },
        "cases": [
            {"key": e.instruction_ref.split("-")[-1] if "-" in e.instruction_ref else e.instruction_ref,
             "decision": e.decision, "reason": e.reason,
             "requested": str(e.requested), "paid": str(e.paid)}
            for e in ledger.entries
        ],
        "totals": {k: str(v) for k, v in t.items()},
    }, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
