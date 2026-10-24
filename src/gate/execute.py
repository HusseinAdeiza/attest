"""The gate — turns an evidence assessment into an ACTION, and records it.

THE THREE LAYERS, AND WHY THEY ARE SEPARATE
    analyst    reads documents          -> claims + citations   (may be a model)
    evidence   scores the claims        -> assessment           (pure code)
    gate       acts on the assessment   -> payment or escalation (this file)

The gate is the only layer that touches money, and it never re-judges the
evidence. It carries out what the scorer decided, and it REFUSES to act at all
if the assessment is missing or malformed. That rule is what stops a bug in the
analyst becoming a payment: a layer that cannot read the assessment cannot pay.

EVERY PATH IS RECORDED, INCLUDING THE REFUSALS
A refusal is the most informative event the system produces — it says the
evidence did not support the instruction, which is exactly what a finance team
needs to see. `Ledger` below keeps every decision with its reason, and the demo
prints the refusals, not just the payments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from src.evidence.score import Assessment, Decision, Reason


class GateError(RuntimeError):
    """Raised when the gate is asked to act on something it cannot justify.

    Deliberately fatal rather than a soft fallback: a gate that guesses when it
    is confused is a gate that pays when it is confused.
    """


# --------------------------------------------------------------------------- #
# The payment rail interface
# --------------------------------------------------------------------------- #

class Rail:
    """What the gate needs from a payment provider. Small on purpose.

    The Airwallex sandbox client implements this; so does the mock used by the
    demo. Because the gate only ever sees this interface, the same gate logic
    is exercised whether or not credentials are configured — the demo cannot
    pass while live fails on a code path the demo never touched.
    """

    def pay(self, *, amount: Decimal, currency: str, payee_name: str,
            payee_account: str, reference: str) -> str:
        """Move the money. Returns a provider reference. Raises on failure."""
        raise NotImplementedError

    def request_approval(self, *, amount: Decimal, currency: str,
                         payee_name: str, reason: str) -> str:
        """Hand to a human. Returns an approval request id."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    at: datetime
    instruction_ref: str
    decision: str
    reason: str
    requested: Decimal
    paid: Decimal
    outcome: str            # what actually happened at the rail
    reference: str = ""     # provider payment id or approval id
    note: str = ""


@dataclass
class Ledger:
    """Append-only record of every decision the gate has made.

    The refusals are the point. A ledger that only recorded successful payments
    would be a receipt book, not an audit trail.
    """
    entries: list[Entry] = field(default_factory=list)

    def record(self, **kw) -> Entry:
        e = Entry(at=datetime.now(timezone.utc), **kw)
        self.entries.append(e)
        return e

    def totals(self) -> dict[str, Decimal]:
        paid = sum((e.paid for e in self.entries), Decimal("0"))
        refused = sum((e.requested for e in self.entries
                       if e.decision == Decision.REFUSE.value), Decimal("0"))
        escalated = sum((e.requested for e in self.entries
                         if e.decision == Decision.ESCALATE.value), Decimal("0"))
        capped = sum((e.requested - e.paid for e in self.entries
                      if e.decision == Decision.AUTHORISE_CAPPED.value),
                     Decimal("0"))
        return {"paid": paid, "refused": refused, "escalated": escalated,
                "withheld_by_cap": capped}

    def render(self) -> str:
        if not self.entries:
            return "  (no decisions recorded)"
        lines = []
        for e in self.entries:
            mark = {"AUTHORISE": "PAID",
                    "AUTHORISE_CAPPED": "PAID (capped)",
                    "ESCALATE": "ESCALATED",
                    "REFUSE": "REFUSED"}.get(e.decision, e.decision)
            amt = f"{e.paid} of {e.requested}" if e.paid != e.requested else f"{e.paid}"
            lines.append(f"  {e.instruction_ref:10s} {mark:15s} {amt:>22s} "
                         f" {e.reason:22s} {e.note}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

@dataclass
class Outcome:
    decision: Decision
    reason: Reason
    paid: Decimal
    reference: str = ""
    note: str = ""


def execute(assessment: Assessment, *, instruction_amount: Decimal,
            currency: str, payee_name: str, payee_account: str,
            reference: str, rail: Rail | None = None,
            ledger: Ledger | None = None) -> Outcome:
    """Carry out an assessment. The ONLY function that moves money.

    Deliberately boring: it switches on the decision and does the obvious
    thing. All judgement happened in `score.assess`; putting judgement here
    too would mean two places could disagree about whether to pay.

    Raises GateError when the assessment is absent or self-inconsistent —
    i.e. when the gate cannot establish what it is supposed to do.
    """
    if assessment is None:
        raise GateError("no assessment: refusing to act without a scored decision")

    decision = assessment.decision
    reason = assessment.reason

    # Guard against a mismatched assessment: a cap above the amount, or a
    # negative figure, means the scorer and the gate disagree about reality.
    if assessment.cap is not None:
        if assessment.cap < 0:
            raise GateError(f"negative cap {assessment.cap}: assessment is malformed")
        if assessment.cap > instruction_amount:
            raise GateError(
                f"cap {assessment.cap} exceeds the requested {instruction_amount}: "
                f"a cap that permits more than asked is not a cap")

    note = "; ".join(assessment.detail[:2]) if assessment.detail else ""

    # ---------------------------------------------------------------- REFUSE
    if decision is Decision.REFUSE:
        if ledger:
            ledger.record(
                instruction_ref=reference, decision=decision.value,
                reason=reason.value, requested=instruction_amount,
                paid=Decimal("0"), outcome="not paid",
                note=note or "evidence did not support payment")
        return Outcome(decision, reason, Decimal("0"),
                       note="no payment attempted")

    # -------------------------------------------------------------- ESCALATE
    if decision is Decision.ESCALATE:
        ref = ""
        if rail:
            ref = rail.request_approval(amount=instruction_amount,
                                        currency=currency,
                                        payee_name=payee_name,
                                        reason=reason.value)
        if ledger:
            ledger.record(
                instruction_ref=reference, decision=decision.value,
                reason=reason.value, requested=instruction_amount,
                paid=Decimal("0"), outcome="awaiting human approval",
                reference=ref, note=note)
        return Outcome(decision, reason, Decimal("0"), reference=ref,
                       note="sent for human approval")

    # ------------------------------------------------------ AUTHORISE (+cap)
    amount = instruction_amount
    if decision is Decision.AUTHORISE_CAPPED:
        if assessment.cap is None:
            raise GateError("AUTHORISE_CAPPED without a cap: refusing to pay full")
        amount = min(amount, assessment.cap)
        note = (f"capped at {amount} of {instruction_amount} requested"
                + (f"; {note}" if note else ""))

    ref = ""
    if rail:
        ref = rail.pay(amount=amount, currency=currency, payee_name=payee_name,
                       payee_account=payee_account, reference=reference)

    if ledger:
        ledger.record(
            instruction_ref=reference, decision=decision.value,
            reason=reason.value, requested=instruction_amount,
            paid=amount, outcome="paid", reference=ref, note=note)

    return Outcome(decision, reason, amount, reference=ref, note=note)
