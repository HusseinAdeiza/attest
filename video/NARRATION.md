"""The ATTEST narration script, timed to beats MEASURED from the take.

BEATS ARE MEASURED, NOT ESTIMATED
Each cue aligns to a pixel change detected in the recording (change seconds:
2,3,4,5,6,12,16,17,20,21,27,28,29,34,37,38) and cross-checked against the demo's
own timestamped log. An earlier video in this series had captions that drifted
ahead of the footage — the narration said one thing while the screen showed
another. A voice that contradicts the screen is worse than no voice.

TOTAL SPOKEN LENGTH IS DELIBERATELY UNDER THE VIDEO LENGTH
The video runs 41s. The narration below totals ~34s of speech, leaving gaps.
Narration that runs wall-to-wall is exhausting to listen to and leaves no room
for a beat to land. The silences are part of the design, not slack.

WHY THE OPENING LINE STATES THE PROBLEM BEFORE THE PRODUCT
A viewer who does not know what is wrong cannot judge what is on screen. The
first eight seconds are the whole problem: the amount is identical, the risk is
not.
"""

from __future__ import annotations

# (start_seconds, text)
# Starts are set a fraction EARLY relative to the screen beat, because speech
# takes a moment to begin and a voice that arrives after the change reads as a
# lag rather than a description.
CUES: list[tuple[float, str]] = [
    # Screen: the instruction block appears. The problem, stated plainly.
    (1.0, "Here is a payment instruction. Twelve thousand dollars to a supplier. "
          "An agent could just pay it."),
    (7.0, "But two identical instructions can carry completely different risk, "
          "and the amount tells you nothing about which is which."),

    # Screen: CASE A — invoice plus purchase order, both 12,000.
    (12.2, "Case A. The invoice and the purchase order that authorised it agree "
           "on every figure."),
    (15.0, "So the agent pays it. In full. No human involved."),

    # Screen: CASE B — invoice alone, capped at 3,000.
    (16.6, "Case B. Same invoice, but the purchase order is gone. One source."),
    (19.0, "The agent still pays, but only a quarter of it, until someone "
           "corroborates the rest."),

    # Screen: CASE C — the PO says 19,000 against the invoice's 12,000.
    (21.0, "Case C. Now the two documents disagree. Nineteen thousand against "
           "twelve."),
    (24.5, "The agent refuses to choose. It escalates to a human and moves "
           "nothing."),

    # Screen: CASE D — an email with nothing to verify it.
    (27.5, "Case D. An email asking for payment, with nothing behind it. "
           "No invoice, no order."),
    (31.0, "Unverifiable. Refused. And recorded as refused."),

    # Screen: the ledger, all four rows and the totals.
    (34.2, "And every decision is kept. Including the ones that moved no money."),
    (37.5, "Same instruction. Same payee. Four outcomes, decided only by the "
           "evidence."),
]

# The captions stay on screen. The narration describes; the captions assert. A
# viewer reading at their own pace and a viewer listening are different people,
# and the video should work for either.
VOICE_HINT = "calm, measured, technical — reporting, not selling"
