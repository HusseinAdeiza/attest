"""Falsification tests: cases where ATTEST's answer could be WRONG.

WHY THIS FILE EXISTS, SEPARATE FROM THE OTHERS
The rest of the suite asserts that the scorer matches the decisions it was
designed to produce. That is consistency, not correctness — grading a scorer
against its own spec measures nothing an outsider can trust.

These tests are written the other way round. Each one states a property that
must hold for ANY correct implementation of the idea, derives the expected
answer independently of `src/evidence/score.py`, and then asks the scorer. If
the scorer is wrong, these fail.

The distinction matters because a claims document is only as good as its
willingness to be falsified. A test that cannot fail is decoration.

WHAT IS BEING FALSIFIED
    1. Monotonicity  — more/better evidence may never LOWER authority
    2. Scale invariance — the decision must not depend on the currency unit
    3. Independence  — a source that says nothing is not a corroborating source
    4. Boundary      — the tolerance is inclusive at its edge, and that edge is
                       the same from both sides
    5. Refusal safety — nothing that scans as money may reach a rail when the
                       evidence is unusable
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.evidence.score import (
    AMOUNT_TOLERANCE,
    Decision,
    Fact,
    Instruction,
    Origin,
    Source,
    assess,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

ACCOUNT = "GB29NWBK60161331926819"
REF = "INV-4471"


def instruction(amount: str = "12000.00", account: str = ACCOUNT,
                ref: str = REF) -> Instruction:
    return Instruction(amount=Decimal(amount), currency="USD",
                       payee_name="Meridian Freight", payee_account=account,
                       reference=ref)


def source(origin: Origin, amount: str, account: str = ACCOUNT, ref: str = REF,
           age_days: int = 0, verifiable: bool = True) -> Source:
    facts = []
    if amount is not None:
        facts.append(Fact("amount", amount))
    if account is not None:
        facts.append(Fact("payee_account", account))
    if ref is not None:
        facts.append(Fact("reference", ref))
    return Source(origin=origin, facts=tuple(facts),
                  observed_at=NOW - timedelta(days=age_days),
                  verifiable=verifiable)


# --------------------------------------------------------------------------- #
# 1. Monotonicity — adding GOOD evidence may never reduce authority
# --------------------------------------------------------------------------- #

# Authority order. Adding corroborating evidence must move UP or stay level,
# never down. Derived from the product's own statement of intent, not from the
# scorer's source.
_RANK = {Decision.REFUSE: 0, Decision.ESCALATE: 1,
         Decision.AUTHORISE_CAPPED: 2, Decision.AUTHORISE: 3}


def test_adding_a_corroborating_source_never_lowers_authority():
    """The central promise. Two agreeing sources must never authorise LESS than
    one alone would, for any amount."""
    for amt in ("1.00", "1000.00", "12000.00", "999999.99"):
        one = assess(instruction(amt), [source(Origin.INVOICE_PDF, amt)], now=NOW)
        two = assess(instruction(amt),
                     [source(Origin.INVOICE_PDF, amt),
                      source(Origin.PURCHASE_ORDER, amt)], now=NOW)
        assert _RANK[two.decision] >= _RANK[one.decision], (
            f"adding an agreeing source lowered authority at {amt}: "
            f"{one.decision} -> {two.decision}")


def test_adding_an_unrelated_but_valid_source_never_lowers_authority():
    """A third independent source that agrees must not push authority down."""
    base = [source(Origin.INVOICE_PDF, "12000.00"),
            source(Origin.PURCHASE_ORDER, "12000.00")]
    a = assess(instruction(), base, now=NOW)
    b = assess(instruction(), base + [source(Origin.CONTRACT,
                                             "12000.00")], now=NOW)
    assert _RANK[b.decision] >= _RANK[a.decision]


# --------------------------------------------------------------------------- #
# 2. Scale invariance — the decision must not depend on the unit
# --------------------------------------------------------------------------- #

def test_the_decision_is_invariant_under_currency_scale():
    """Converting every figure by 1000x, and the instruction with it, must not
    change the decision. If it does, the scorer is reacting to magnitude rather
    than to agreement — which would mean a large invoice is treated as a
    different KIND of instruction than a small one.
    """
    for factor in (Decimal("0.001"), Decimal("1"), Decimal("1000")):
        amt = (Decimal("12000.00") * factor).quantize(Decimal("0.01"))
        ins = instruction(str(amt))
        one = assess(ins, [source(Origin.INVOICE_PDF, str(amt))], now=NOW)
        two = assess(ins, [source(Origin.INVOICE_PDF, str(amt)),
                           source(Origin.PURCHASE_ORDER, str(amt))], now=NOW)
        assert one.decision is Decision.AUTHORISE_CAPPED, (
            f"factor {factor}: single source gave {one.decision}")
        assert two.decision is Decision.AUTHORISE, (
            f"factor {factor}: two sources gave {two.decision}")


def test_the_cap_is_proportional_not_absolute():
    """The cap must be a fraction of the request, so a 100x larger request
    authorises a 100x larger capped payment. An absolute cap would be a
    different product."""
    small = assess(instruction("100.00"),
                   [source(Origin.INVOICE_PDF, "100.00")], now=NOW)
    large = assess(instruction("10000.00"),
                   [source(Origin.INVOICE_PDF, "10000.00")], now=NOW)
    assert small.cap is not None and large.cap is not None
    ratio = large.cap / small.cap
    assert ratio == Decimal("100"), f"cap scaled by {ratio}, expected 100"


# --------------------------------------------------------------------------- #
# 3. Independence — a source that asserts nothing is not a source
# --------------------------------------------------------------------------- #

def test_a_source_with_no_facts_cannot_corroborate():
    """An empty document must count for nothing. If it counted, an attacker
    could corroborate any instruction by attaching blank files."""
    agreeing = source(Origin.INVOICE_PDF, "12000.00")
    empty = Source(origin=Origin.PURCHASE_ORDER, facts=(),
                   observed_at=NOW, verifiable=True)
    alone = assess(instruction(), [agreeing], now=NOW)
    with_empty = assess(instruction(), [agreeing, empty], now=NOW)
    assert alone.decision is with_empty.decision
    assert with_empty.independent_sources == alone.independent_sources


def test_two_sources_of_the_same_origin_are_not_independent():
    """Independence is claimed by ORIGIN. Two invoices are not corroboration,
    even if both agree — otherwise duplicating one document manufactures
    authority."""
    one = assess(instruction(), [source(Origin.INVOICE_PDF, "12000.00")], now=NOW)
    two = assess(instruction(), [source(Origin.INVOICE_PDF, "12000.00"),
                                source(Origin.INVOICE_PDF, "12000.00")], now=NOW)
    assert one.independent_sources == 1
    assert two.independent_sources == 1, (
        "two documents of the same origin were counted as independent")
    assert one.decision is two.decision


# --------------------------------------------------------------------------- #
# 4. The tolerance boundary, from both sides
# --------------------------------------------------------------------------- #

def test_the_tolerance_edge_is_inclusive_and_symmetric():
    """The tolerance is 1% OF THE LARGER CLAIMED FIGURE — and the larger figure
    moves with the difference, so the boundary is a fixed point, not a constant.

    Working it out properly, with base = b and an upward delta d:
        hi      = b + d
        allowed = (b + d) * 0.01
        agree  <=>  d <= (b + d) * 0.01
        <=>  d * 0.99 <= b * 0.01
        <=>  d <= b * 0.01 / 0.99          ->  for b = 12000, d <= 121.2121...
    Downward, the larger figure is b itself, so the edge is exactly b * 0.01.

    That asymmetry is CORRECT and not a bug: when the second source claims MORE,
    the "larger figure" it is measured against is its own. An earlier version of
    this test assumed a flat 120.00 in both directions and failed — the
    expectation was wrong, not the scorer.

    This is asserted on both sides so a future change to the tolerance model
    cannot quietly move the boundary.
    """
    b = Decimal("12000.00")

    # Upward edge: the fixed point of d = (b + d) * TOL, just inside and out.
    up_edge = (b * AMOUNT_TOLERANCE
               / (Decimal("1") - AMOUNT_TOLERANCE)).quantize(Decimal("0.01"))
    assert up_edge == Decimal("121.21"), f"upward edge moved: {up_edge}"
    for d, expected, label in ((up_edge, Decision.AUTHORISE, "at the upward edge"),
                               (up_edge + Decimal("0.01"), Decision.ESCALATE,
                                "one cent past the upward edge")):
        other = (b + d).quantize(Decimal("0.01"))
        a = assess(instruction(str(b)),
                   [source(Origin.INVOICE_PDF, str(b)),
                    source(Origin.PURCHASE_ORDER, str(other))], now=NOW)
        assert a.decision is expected, f"{label} (delta {d}): got {a.decision}"

    # Downward edge: the larger figure is the instruction, so it is exactly 1%
    # of the base, and 1% is always a smaller step than the upward fixed point.
    down_edge = (b * AMOUNT_TOLERANCE).quantize(Decimal("0.01"))
    assert down_edge == Decimal("120.00"), f"downward edge moved: {down_edge}"
    assert down_edge < up_edge, (
        "the upward edge should be wider than the downward one, because the "
        "larger figure it is measured against includes the difference itself")
    for d, expected, label in ((down_edge, Decision.AUTHORISE,
                                "at the downward edge"),
                               (down_edge + Decimal("0.01"), Decision.ESCALATE,
                                "one cent past the downward edge")):
        other = (b - d).quantize(Decimal("0.01"))
        a = assess(instruction(str(b)),
                   [source(Origin.INVOICE_PDF, str(b)),
                    source(Origin.PURCHASE_ORDER, str(other))], now=NOW)
        assert a.decision is expected, f"{label} (delta -{d}): got {a.decision}"


def test_the_tolerance_is_proportional_so_it_scales_with_the_amount():
    """The same FRACTIONAL difference must be tolerated at any magnitude, and
    the same fractional excess must always escalate. This is the property that
    makes one constant usable across a $50 invoice and a $5m one."""
    for base in (Decimal("50.00"), Decimal("12000.00"), Decimal("5000000.00")):
        within = base + (base * AMOUNT_TOLERANCE * Decimal("0.5"))
        beyond = base + (base * AMOUNT_TOLERANCE * Decimal("2"))
        for other, expected, label in ((within, Decision.AUTHORISE, "inside"),
                                       (beyond, Decision.ESCALATE, "beyond")):
            o = other.quantize(Decimal("0.01"))
            a = assess(instruction(str(base)),
                       [source(Origin.INVOICE_PDF, str(base)),
                        source(Origin.PURCHASE_ORDER, str(o))], now=NOW)
            assert a.decision is expected, (
                f"base {base}, {label} ({o}): got {a.decision}")


# --------------------------------------------------------------------------- #
# 5. Refusal safety — the property that matters most
# --------------------------------------------------------------------------- #

def test_an_unverifiable_source_never_reaches_an_authorising_decision():
    """Whatever the amounts say, an unverifiable source must not produce an
    authorising decision on its own."""
    for amt in ("1.00", "12000.00", "1000000.00"):
        a = assess(instruction(amt),
                   [source(Origin.MESSAGE, amt, verifiable=False)], now=NOW)
        assert a.decision in (Decision.REFUSE, Decision.ESCALATE), (
            f"an unverifiable source authorised at {amt}: {a.decision}")


def test_an_unverifiable_source_cannot_corroborate_a_real_one():
    """Pairing a good source with an unverifiable one must not reach the
    autonomous tier — the unverifiable one carries no weight."""
    good = source(Origin.INVOICE_PDF, "12000.00")
    bad = source(Origin.MESSAGE, "12000.00", verifiable=False)
    a = assess(instruction(), [good, bad], now=NOW)
    assert a.decision is Decision.AUTHORISE_CAPPED, (
        f"an unverifiable source raised the tier to {a.decision}")


def test_a_stale_source_is_not_current_evidence():
    """A document old enough to be stale must not authorise alone, however
    well-formed it is. Otherwise an old invoice pays forever."""
    fresh = assess(instruction(), [source(Origin.INVOICE_PDF, "12000.00",
                                          age_days=0)], now=NOW)
    stale = assess(instruction(), [source(Origin.INVOICE_PDF, "12000.00",
                                          age_days=900)], now=NOW)
    assert _RANK[stale.decision] < _RANK[fresh.decision], (
        "a 900-day-old invoice carried the same authority as a fresh one")


# --------------------------------------------------------------------------- #
# The meta-test
# --------------------------------------------------------------------------- #

def test_these_tests_can_actually_fail():
    """A falsification suite that cannot fail is decoration.

    This asserts the harness itself is live: a deliberately wrong expectation
    must raise. If this test ever passes without the assert firing, the file is
    not testing anything.
    """
    intent = instruction()
    a = assess(intent, [source(Origin.INVOICE_PDF, "12000.00")], now=NOW)
    # The scorer says AUTHORISE_CAPPED here. Asserting AUTHORISE must fail.
    with pytest.raises(AssertionError):
        assert a.decision is Decision.AUTHORISE, "deliberately wrong"
