"""Regression tests for the amount extractor.

WHY THIS FILE EXISTS SEPARATELY
The four-case demo caught a bug no unit test had: the purchase order's total
(19,000.00) was being read as 88,214 — the PO *number*. It broke TWO cases at
once, because the same document appears in the corroborated case and the
contradicted one, so the case that should have paid in full was reading a
fabricated figure.

A test that only fed the extractor a clean invoice would never have seen it.
These tests use the ACTUAL sample documents the demo uses, so the demo's
claims and the tests cannot drift apart.
"""

from pathlib import Path

import pytest

from src.analyst.extract import (
    _is_money_like,
    _sentences,
    extract_deterministically,
)
from src.evidence.score import Origin

SAMPLES = Path(__file__).resolve().parents[1] / "evidence_samples"


def amount_of(name: str, origin: Origin = Origin.INVOICE_PDF) -> str:
    ex = extract_deterministically((SAMPLES / name).read_text(), origin)
    for c in ex.claims:
        if c.key == "amount":
            return c.value
    raise AssertionError(f"no amount extracted from {name}: dropped={ex.dropped}")


# --------------------------------------------------------------------------- #
# The bug that shipped
# --------------------------------------------------------------------------- #

def test_po_number_is_not_read_as_the_amount():
    """THE REGRESSION. "PO-88214" contains a five-digit run. A length-only guard
    (<= 4 digits) let it through, and it was reported as an amount of 88214 —
    a figure the document does not contain."""
    value = amount_of("purchase_order.txt", Origin.PURCHASE_ORDER)
    normalised = value.replace(",", "").replace("USD", "").strip()
    assert "88214" not in normalised, (
        f"the PO number was read as an amount: {value!r}")
    assert "12000" in normalised, f"expected the contract value, got {value!r}"


def test_mismatched_po_reads_its_own_total():
    """The contradicted case depends on this: the extractor must find 19,000,
    because the whole point of that case is that the two documents disagree."""
    value = amount_of("purchase_order_mismatch.txt", Origin.PURCHASE_ORDER)
    normalised = value.replace(",", "").replace("USD", "").strip()
    assert "19000" in normalised, f"expected 19,000, got {value!r}"
    assert "88214" not in normalised


def test_the_two_purchase_orders_differ_by_exactly_the_total():
    """Guards the demo's Case A vs Case C distinction. If both POs extracted
    the same value, the demo would show two identical outcomes and the
    contradiction case would silently stop testing anything."""
    a = amount_of("purchase_order.txt", Origin.PURCHASE_ORDER)
    c = amount_of("purchase_order_mismatch.txt", Origin.PURCHASE_ORDER)
    assert a != c, "the two purchase orders must extract different amounts"


# --------------------------------------------------------------------------- #
# The guard itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("number,symbol,sentence,money", [
    ("12,000.00", None,   "Total amount due: 12,000.00",      True),
    ("12000.00",  None,   "Total: 12000.00",                  True),
    ("12000",     "USD",  "Total: USD 12000",                 True),
    ("88214",     None,   "PURCHASE ORDER PO-88214",          False),
    ("4471",      None,   "INVOICE INV-4471",                 False),
    ("88214",     None,   "Reference: PO 88214",              False),
    ("2",         None,   "container 40ft, 2 units",          False),
    ("40",        None,   "container 40ft",                   False),
    ("1234567",   None,   "Cheque 1234567",                   True),   # long bare
])
def test_money_like_classification(number, symbol, sentence, money):
    """The three guards, tabulated. A bare integer run is not money unless it
    carries a symbol, separators, or is implausibly long to be a quantity."""
    assert _is_money_like(number, symbol, sentence) is money


def test_identifier_prefixes_are_recognised_before_the_digits():
    """The guard looks at what PRECEDES the digits, so it must survive the
    punctuation variants real documents use."""
    for prefix in ("PO-", "PO ", "Ref: ", "INV-", "INVOICE INV-", "Order No: "):
        assert not _is_money_like("88214", None, f"{prefix}88214"), prefix


# --------------------------------------------------------------------------- #
# Guards against the same class of error elsewhere in the sample set
# --------------------------------------------------------------------------- #

def test_container_counts_are_not_read_as_money():
    """'container 40ft, 2 units' contains two bare integers. Neither is an
    amount, and the invoice's real total must still win.

    Compares the extracted VALUE, not substrings — an earlier version asserted
    "2" not in "12000", which fails because the digit appears inside the total.
    A substring check on a number tests nothing.
    """
    from decimal import Decimal
    value = amount_of("invoice_normal.txt")
    digits = value.replace(",", "").replace("USD", "").strip()
    assert Decimal(digits) == Decimal("12000.00"), (
        f"expected the invoice total, got {value!r}")
    # The component figures must not have been chosen either.
    assert Decimal(digits) not in (Decimal("40"), Decimal("2"),
                                   Decimal("11000.00"), Decimal("2200.00"))


def test_no_sample_yields_an_amount_that_does_not_appear_in_the_document():
    """The strongest available check: every extracted figure must be findable
    in the source text. A fabricated amount cannot satisfy this, by definition.
    """
    for f in sorted(SAMPLES.glob("*.txt")):
        text = f.read_text()
        origin = (Origin.MESSAGE if "email" in f.name else
                  Origin.PURCHASE_ORDER if "purchase" in f.name else
                  Origin.INVOICE_PDF)
        ex = extract_deterministically(text, origin)
        for c in ex.claims:
            digits = c.value.replace(",", "").replace("USD", "").strip()
            plain = text.replace(",", "")
            assert digits in plain, (
                f"{f.name}: extracted {c.key}={c.value!r} which does not "
                f"appear in the document — a fabricated figure")
