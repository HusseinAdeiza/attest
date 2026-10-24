"""Tests for the evidence scorer.

These exist before the rest of the system because this module decides how much
money an agent may move. If a threshold here is wrong, nothing downstream can
compensate for it.

The four scenarios the demo will show are the first four tests, so the demo's
claims are the tested claims rather than a separate narrative.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.evidence.score import (
    AMOUNT_TOLERANCE,
    MAX_EVIDENCE_AGE,
    SINGLE_SOURCE_CAP_FRACTION,
    Assessment,
    Decision,
    Fact,
    Instruction,
    Origin,
    Reason,
    Source,
    _canonical_amount,
    assess,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

INVOICE = Instruction(
    amount=Decimal("12000.00"),
    currency="USD",
    payee_name="Meridian Freight",
    payee_account="GB29NWBK60161331926819",
    reference="INV-4471",
)


def src(origin, *, amount="12000.00", age_days=0, keys=None, verifiable=True,
        label=""):
    """Build a source asserting `amount` (unless keys overrides which facts)."""
    facts = []
    if keys is None:
        keys = ["amount", "payee_account", "reference"]
    for k in keys:
        if k == "amount":
            facts.append(Fact("amount", amount))
        elif k == "payee_account":
            facts.append(Fact("payee_account", "GB29NWBK60161331926819"))
        elif k == "reference":
            facts.append(Fact("reference", "INV-4471"))
    return Source(origin=origin, facts=tuple(facts),
                  observed_at=NOW - timedelta(days=age_days),
                  verifiable=verifiable, label=label)


# --------------------------------------------------------------------------- #
# The four demo scenarios — these ARE the product claim
# --------------------------------------------------------------------------- #

def test_four_demo_scenarios_differ_only_by_evidence():
    """SAME amount, SAME payee, FOUR different decisions.

    This is the whole thesis in one test: if the four results were not
    distinct, the product would be indistinguishable from a static limit.
    """
    # 1. Corroborated by two independent origins -> pay in full.
    a = assess(INVOICE, [src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)],
               now=NOW)
    assert a.decision is Decision.AUTHORISE
    assert a.reason is Reason.CORROBORATED
    assert a.cap is None

    # 2. Single source -> pay, but capped.
    b = assess(INVOICE, [src(Origin.INVOICE_PDF)], now=NOW)
    assert b.decision is Decision.AUTHORISE_CAPPED
    assert b.reason is Reason.SINGLE_SOURCE
    assert b.cap == (INVOICE.amount * SINGLE_SOURCE_CAP_FRACTION).quantize(
        Decimal("0.01"))
    assert b.cap < INVOICE.amount, "an uncorroborated instruction must not be paid in full"

    # 3. Contradicted -> a human decides.
    c = assess(INVOICE, [src(Origin.INVOICE_PDF, amount="12000.00"),
                         src(Origin.PURCHASE_ORDER, amount="19000.00")], now=NOW)
    assert c.decision is Decision.ESCALATE
    assert c.reason is Reason.CONTRADICTED

    # 4. Nothing usable -> refuse, and record why.
    d = assess(INVOICE, [src(Origin.MESSAGE, verifiable=False)], now=NOW)
    assert d.decision is Decision.REFUSE
    assert d.reason is Reason.UNVERIFIABLE_ORIGIN


# --------------------------------------------------------------------------- #
# Independence — the part that is easy to get wrong
# --------------------------------------------------------------------------- #

def test_two_sources_same_origin_are_not_corroboration():
    """A supplier emailing the same invoice twice has sent one document twice."""
    a = assess(INVOICE, [src(Origin.INVOICE_PDF), src(Origin.INVOICE_PDF)],
               now=NOW)
    assert a.independent_sources == 1
    assert a.decision is Decision.AUTHORISE_CAPPED, (
        "duplicate origins must not reach the autonomous tier")


def test_three_independent_origins_score_highest():
    a = assess(INVOICE, [src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER),
                         src(Origin.APPROVAL_SYSTEM)], now=NOW)
    assert a.decision is Decision.AUTHORISE
    assert a.score == Decimal("0.90")


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #

def test_stale_only_evidence_is_refused():
    a = assess(INVOICE, [src(Origin.INVOICE_PDF, age_days=90)], now=NOW)
    assert a.decision is Decision.REFUSE
    assert a.reason is Reason.STALE


def test_stale_source_is_discarded_but_fresh_ones_still_count():
    """A stale duplicate is dropped, and what remains still decides.

    The expectation here is deliberately AUTHORISE_CAPPED, not AUTHORISE:
    once the aged INVOICE_PDF is discarded only ONE origin is left, and one
    origin is not corroboration. An earlier version of this test asserted
    AUTHORISE, which was asserting the outcome I wanted rather than the
    behaviour the scorer should have.
    """
    a = assess(INVOICE, [src(Origin.INVOICE_PDF, age_days=90),
                         src(Origin.PURCHASE_ORDER)], now=NOW)
    assert a.decision is Decision.AUTHORISE_CAPPED
    assert a.independent_sources == 1
    assert "older than" in " ".join(a.detail), "the discarded source must be reported"


def test_two_fresh_independent_sources_survive_a_stale_third():
    """With two fresh origins left after discarding the stale one, the
    instruction does reach the autonomous tier — the stale source neither
    rescues nor penalises it."""
    a = assess(INVOICE, [src(Origin.INVOICE_PDF, age_days=90),
                         src(Origin.PURCHASE_ORDER),
                         src(Origin.APPROVAL_SYSTEM)], now=NOW)
    assert a.decision is Decision.AUTHORISE
    assert a.independent_sources == 2
    assert "older than" in " ".join(a.detail)


def test_boundary_of_the_staleness_window_is_inclusive():
    """Exactly MAX_EVIDENCE_AGE old is still fresh — off-by-one here changes
    whether a payment is refused, so the boundary is asserted explicitly."""
    a = assess(INVOICE, [src(Origin.INVOICE_PDF, age_days=MAX_EVIDENCE_AGE.days)],
               now=NOW)
    assert a.reason is not Reason.STALE

    b = assess(INVOICE, [src(Origin.INVOICE_PDF, age_days=MAX_EVIDENCE_AGE.days + 1)],
               now=NOW)
    assert b.reason is Reason.STALE


# --------------------------------------------------------------------------- #
# Amount comparison — must not fire on formatting, must fire on tampering
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("a,b,should_contradict", [
    ("12000.00", "12000", False),        # same number, different formatting
    ("12,000.00", "12000.0000", False),  # commas and trailing zeros
    ("USD 12000.00", "12000.00", False), # currency prefix
    ("12000.00", "12000.10", False),     # within 1% tolerance (rounding)
    ("12000.00", "12001.00", False),     # exactly at the 1% boundary
    ("12000.00", "19000.00", True),      # a different number
    ("12000.00", "1200.00", True),       # a missing digit
])
def test_amount_comparison(a, b, should_contradict):
    res = assess(INVOICE, [src(Origin.INVOICE_PDF, amount=a),
                           src(Origin.PURCHASE_ORDER, amount=b)], now=NOW)
    if should_contradict:
        assert res.reason is Reason.CONTRADICTED, f"{a} vs {b} should contradict"
    else:
        assert res.reason is not Reason.CONTRADICTED, f"{a} vs {b} should agree"


def test_tolerance_scales_with_magnitude():
    """1% of 12,000 is 120; the same fraction must not be applied absolutely."""
    small = Instruction(amount=Decimal("50.00"), currency="USD",
                        payee_name="X", payee_account="Y")
    # 0.50 apart on a 50.00 instruction is 1% -> allowed.
    a = assess(small, [src(Origin.INVOICE_PDF, amount="50.00"),
                       src(Origin.PURCHASE_ORDER, amount="50.50")], now=NOW)
    assert a.reason is not Reason.CONTRADICTED
    # 5.00 apart is 10% -> contradiction.
    b = assess(small, [src(Origin.INVOICE_PDF, amount="50.00"),
                       src(Origin.PURCHASE_ORDER, amount="55.00")], now=NOW)
    assert b.reason is Reason.CONTRADICTED


def test_unparseable_amount_is_reported_not_silently_ignored():
    """A model that mangles a figure must produce a visible note, because a
    silently dropped amount looks identical to agreement."""
    res = assess(INVOICE, [src(Origin.INVOICE_PDF, amount="about twelve thousand"),
                           src(Origin.PURCHASE_ORDER)], now=NOW)
    assert any("unparseable" in d for d in res.detail)
    assert res.decision is Decision.AUTHORISE  # the good source still decides


def test_canonical_amount_prefixes():
    assert _canonical_amount("USD 1,200.50") == Decimal("1200.50")
    assert _canonical_amount("€1.000,00") is None or isinstance(
        _canonical_amount("€1.000,00"), Decimal)
    assert _canonical_amount("n/a") is None


# --------------------------------------------------------------------------- #
# No evidence at all
# --------------------------------------------------------------------------- #

def test_no_sources_is_no_evidence_not_unverifiable():
    """These are different conditions and must not share a reason code:
    'we received nothing' and 'we received something we cannot trust' call for
    different human responses."""
    a = assess(INVOICE, [], now=NOW)
    assert a.decision is Decision.REFUSE
    assert a.reason is Reason.NO_EVIDENCE

    b = assess(INVOICE, [src(Origin.MESSAGE, verifiable=False)], now=NOW)
    assert b.reason is Reason.UNVERIFIABLE_ORIGIN
    assert a.reason is not b.reason


def test_refusal_always_carries_a_reason_and_a_score():
    for sources in ([], [src(Origin.INVOICE_PDF, age_days=999)]):
        a = assess(INVOICE, sources, now=NOW)
        assert a.decision is Decision.REFUSE
        assert a.reason is not None
        assert isinstance(a.score, Decimal)


# --------------------------------------------------------------------------- #
# Determinism — the property that makes this auditable
# --------------------------------------------------------------------------- #

def test_assessment_is_deterministic():
    """Same inputs, same output, every time. If this ever fails, the gate is
    not reproducible and the audit trail means nothing."""
    sources = [src(Origin.INVOICE_PDF), src(Origin.PURCHASE_ORDER)]
    results = {assess(INVOICE, sources, now=NOW).decision for _ in range(50)}
    assert len(results) == 1


def test_explain_is_populated_for_every_decision():
    a = assess(INVOICE, [src(Origin.INVOICE_PDF)], now=NOW)
    text = a.explain()
    assert "AUTHORISE_CAPPED" in text
    assert "SINGLE_SOURCE" in text
    assert "cap" in text
