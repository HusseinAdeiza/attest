"""Tests for the gate and the rails.

The gate is the only code that moves money, so its tests are about what it
REFUSES to do as much as what it does. Several of these assert a raise: a gate
that guesses when it is confused is a gate that pays when it is confused.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.evidence.score import (
    Assessment,
    Decision,
    Fact,
    Instruction,
    Origin,
    Reason,
    Source,
    assess,
)
from src.gate.execute import GateError, Ledger, execute
from src.rails.payment import MockRail, build_rail

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

INSTRUCTION = Instruction(
    amount=Decimal("12000.00"), currency="USD",
    payee_name="Meridian Freight",
    payee_account="GB29NWBK60161331926819", reference="INV-4471")


def src(origin, amount="12000.00", age_days=0, verifiable=True):
    from datetime import timedelta
    return Source(
        origin=origin,
        facts=(Fact("amount", amount),
               Fact("payee_account", "GB29NWBK60161331926819"),
               Fact("reference", "INV-4471")),
        observed_at=NOW - timedelta(days=age_days),
        verifiable=verifiable)


def run(sources, rail=None, ledger=None):
    a = assess(INSTRUCTION, sources, now=NOW)
    return execute(a, instruction_amount=INSTRUCTION.amount,
                   currency=INSTRUCTION.currency,
                   payee_name=INSTRUCTION.payee_name,
                   payee_account=INSTRUCTION.payee_account,
                   reference=INSTRUCTION.reference, rail=rail, ledger=ledger), a


# --------------------------------------------------------------------------- #
# The four paths, end to end
# --------------------------------------------------------------------------- #

def test_corroborated_instruction_pays_in_full():
    rail, ledger = MockRail(), Ledger()
    out, a = run([src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)],
                 rail=rail, ledger=ledger)
    assert out.decision is Decision.AUTHORISE
    assert out.paid == Decimal("12000.00")
    assert rail.balance == Decimal("100000.00") - Decimal("12000.00")
    assert len(rail.payments) == 1
    assert out.reference.startswith("MOCKPAY")


def test_single_source_pays_only_the_cap_and_the_ledger_shows_the_shortfall():
    """The central money-saving behaviour: an uncorroborated invoice moves a
    QUARTER of the requested amount, and the ledger records both figures so the
    shortfall is visible rather than implied."""
    rail, ledger = MockRail(), Ledger()
    out, a = run([src(Origin.INVOICE_PDF)], rail=rail, ledger=ledger)

    assert out.decision is Decision.AUTHORISE_CAPPED
    assert out.paid == Decimal("3000.00")          # 25% of 12,000
    assert out.paid < INSTRUCTION.amount
    assert rail.payments[0]["amount"] == "3000.00"

    t = ledger.totals()
    assert t["paid"] == Decimal("3000.00")
    assert t["withheld_by_cap"] == Decimal("9000.00")


def test_contradicted_instruction_pays_nothing_and_asks_a_human():
    rail, ledger = MockRail(), Ledger()
    out, _ = run([src(Origin.INVOICE_PDF, amount="12000.00"),
                  src(Origin.PURCHASE_ORDER, amount="19000.00")],
                 rail=rail, ledger=ledger)
    assert out.decision is Decision.ESCALATE
    assert out.paid == Decimal("0")
    assert rail.payments == [], "an escalated instruction must not move money"
    assert len(rail.approvals) == 1
    assert out.reference.startswith("MOCKAPPR")


def test_no_evidence_refuses_and_touches_nothing():
    rail, ledger = MockRail(), Ledger()
    out, _ = run([], rail=rail, ledger=ledger)
    assert out.decision is Decision.REFUSE
    assert out.paid == Decimal("0")
    assert rail.payments == [] and rail.approvals == []
    assert ledger.totals()["refused"] == Decimal("12000.00")


def test_all_four_outcomes_are_distinct_for_the_same_instruction():
    """The product claim in one test: same amount, same payee, four different
    outcomes decided only by the evidence."""
    rail = MockRail()
    results = [
        run([src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)], rail=rail)[0],
        run([src(Origin.INVOICE_PDF)], rail=rail)[0],
        run([src(Origin.INVOICE_PDF, amount="12000.00"),
             src(Origin.PURCHASE_ORDER, amount="19000.00")], rail=rail)[0],
        run([src(Origin.MESSAGE, verifiable=False)], rail=rail)[0],
    ]
    decisions = [r.decision for r in results]
    assert decisions == [Decision.AUTHORISE, Decision.AUTHORISE_CAPPED,
                         Decision.ESCALATE, Decision.REFUSE]
    paid = [r.paid for r in results]
    assert paid[0] > paid[1] > paid[2] == paid[3] == 0


# --------------------------------------------------------------------------- #
# The gate refuses to act on a bad assessment
# --------------------------------------------------------------------------- #

def test_gate_refuses_without_an_assessment():
    with pytest.raises(GateError):
        execute(None, instruction_amount=Decimal("1"), currency="USD",
                payee_name="X", payee_account="Y", reference="R")


def test_gate_refuses_a_capped_decision_with_no_cap():
    """A malformed assessment must not become an uncapped payment."""
    bad = Assessment(Decision.AUTHORISE_CAPPED, Reason.SINGLE_SOURCE,
                     Decimal("0.4"), None, 1, 1)
    with pytest.raises(GateError):
        execute(bad, instruction_amount=Decimal("100"), currency="USD",
                payee_name="X", payee_account="Y", reference="R")


def test_gate_refuses_a_cap_larger_than_the_instruction():
    """A cap that permits MORE than requested is not a cap, and indicates the
    scorer and the gate disagree about the amount."""
    bad = Assessment(Decision.AUTHORISE_CAPPED, Reason.SINGLE_SOURCE,
                     Decimal("0.4"), Decimal("99999"), 1, 1)
    with pytest.raises(GateError):
        execute(bad, instruction_amount=Decimal("100"), currency="USD",
                payee_name="X", payee_account="Y", reference="R")


def test_gate_refuses_a_negative_cap():
    bad = Assessment(Decision.AUTHORISE_CAPPED, Reason.SINGLE_SOURCE,
                     Decimal("0.4"), Decimal("-5"), 1, 1)
    with pytest.raises(GateError):
        execute(bad, instruction_amount=Decimal("100"), currency="USD",
                payee_name="X", payee_account="Y", reference="R")


# --------------------------------------------------------------------------- #
# The rail's own controls still apply
# --------------------------------------------------------------------------- #

def test_rail_ceiling_is_not_bypassed_by_a_permissive_assessment():
    """Even when the evidence fully authorises a payment, the RAIL may refuse
    it. The gate must not swallow that: a provider rejection is a real outcome
    and must surface, not be reported as a successful transfer."""
    rail = MockRail()
    big = Instruction(amount=Decimal("90000.00"), currency="USD",
                      payee_name="Big Co", payee_account="GB00TEST",
                      reference="INV-BIG")
    a = Assessment(Decision.AUTHORISE, Reason.CORROBORATED, Decimal("0.9"),
                   None, 2, 2)
    with pytest.raises(ValueError, match="per-transfer ceiling"):
        execute(a, instruction_amount=big.amount, currency=big.currency,
                payee_name=big.payee_name, payee_account=big.payee_account,
                reference=big.reference, rail=rail)
    assert rail.payments == [], "a refused rail call must not be recorded as paid"


def test_rail_refuses_insufficient_balance():
    rail = MockRail(starting_balance=Decimal("100.00"))
    a = Assessment(Decision.AUTHORISE, Reason.CORROBORATED, Decimal("0.9"),
                   None, 2, 2)
    with pytest.raises(ValueError, match="insufficient balance"):
        execute(a, instruction_amount=Decimal("12000.00"), currency="USD",
                payee_name="X", payee_account="Y", reference="R", rail=rail)


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #

def test_ledger_records_refusals_not_just_payments():
    """A receipt book is not an audit trail. The refusals are what a finance
    team reads."""
    rail, ledger = MockRail(), Ledger()
    run([src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)], rail=rail, ledger=ledger)
    run([], rail=rail, ledger=ledger)
    run([src(Origin.INVOICE_PDF)], rail=rail, ledger=ledger)

    assert len(ledger.entries) == 3
    decisions = [e.decision for e in ledger.entries]
    assert Decision.REFUSE.value in decisions
    text = ledger.render()
    assert "REFUSED" in text and "PAID" in text


def test_ledger_totals_add_up():
    rail, ledger = MockRail(), Ledger()
    run([src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)], rail=rail, ledger=ledger)
    run([src(Origin.INVOICE_PDF)], rail=rail, ledger=ledger)
    run([], rail=rail, ledger=ledger)
    t = ledger.totals()
    assert t["paid"] == Decimal("12000.00") + Decimal("3000.00")
    assert t["refused"] == Decimal("12000.00")
    assert t["withheld_by_cap"] == Decimal("9000.00")


def test_rail_selection_reports_which_rail_ran():
    """The label exists so a run cannot claim a live payment it did not make."""
    rail, label = build_rail(prefer_live=True)
    assert isinstance(rail, MockRail)
    assert label == "mock", "with no credentials the label must say mock"
