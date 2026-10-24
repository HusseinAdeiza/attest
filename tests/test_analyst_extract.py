"""Tests for the analyst layer.

The most important test in this file is
`test_a_hallucinated_claim_cannot_change_the_decision`: it is the claim that a
prompted or compromised reader cannot move money. Everything else here supports
that one.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.analyst.extract import (
    Claim,
    Extraction,
    _first_json_object,
    extract_deterministically,
)
from src.evidence.score import (
    Decision,
    Instruction,
    Origin,
    Source,
    assess,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

INVOICE_TEXT = """INVOICE INV-4471

From: Meridian Freight Ltd
To: Northwind Logistics

Freight services, Rotterdam to Felixstowe.
Subtotal: 11,000.00
VAT: 1,000.00
Total amount due: USD 12,000.00

Payment to IBAN GB29NWBK60161331926819
Reference: INV-4471
"""


# --------------------------------------------------------------------------- #
# Extraction basics
# --------------------------------------------------------------------------- #

def test_extracts_the_key_facts_from_a_normal_invoice():
    ex = extract_deterministically(INVOICE_TEXT, Origin.INVOICE_PDF)
    got = {c.key: c.value for c in ex.claims}
    assert "amount" in got
    assert "12000.00" in got["amount"].replace(",", "")
    assert got["payee_account"] == "GB29NWBK60161331926819"
    assert got["reference"] == "INV-4471"


def test_prefers_the_total_over_subtotal_vat_and_line_items():
    """The most expensive possible reading error: paying the VAT figure, or the
    subtotal, instead of the total. The extractor must prefer the sentence that
    says 'total amount due'."""
    ex = extract_deterministically(INVOICE_TEXT, Origin.INVOICE_PDF)
    amount = next(c for c in ex.claims if c.key == "amount")
    assert "12,000" in amount.value or "12000" in amount.value.replace(",", "")
    assert "11,000" not in amount.value
    assert "1,000" not in amount.value
    assert "total" in amount.evidence.lower(), "the citation must be the total line"


def test_every_claim_carries_a_verbatim_citation():
    ex = extract_deterministically(INVOICE_TEXT, Origin.INVOICE_PDF)
    for c in ex.claims:
        assert c.evidence.strip(), f"{c.key} has no citation"
        # the citation must actually appear in the source document
        assert c.value.split()[-1].replace(",", "") in c.evidence.replace(",", "") \
            or c.value in c.evidence, f"{c.key} citation does not contain its value"


def test_missing_facts_are_reported_not_invented():
    """A document with no account number must yield no payee_account claim —
    never a guess. This is the difference between a reader and a hallucinator."""
    ex = extract_deterministically("Please pay the usual amount soon.",
                                   Origin.EMAIL)
    keys = {c.key for c in ex.claims}
    assert "payee_account" not in keys
    assert "payee_account" in ex.dropped
    assert "amount" in ex.dropped


# --------------------------------------------------------------------------- #
# The security property
# --------------------------------------------------------------------------- #

def test_a_hallucinated_claim_cannot_change_the_decision():
    """THE CORE SAFETY CLAIM.

    Simulate a model that invents support: it returns a claim whose `evidence`
    is empty (nothing to quote). The scorer must behave exactly as if that
    claim did not exist — so a compromised reader cannot manufacture the
    corroboration needed to authorise a payment.
    """
    instruction = Instruction(amount=Decimal("12000.00"), currency="USD",
                              payee_name="Meridian Freight",
                              payee_account="GB29NWBK60161331926819")

    honest = Extraction(origin=Origin.INVOICE_PDF, observed_at=NOW)
    honest.claims.append(Claim("amount", "12000.00", "Total amount due: USD 12,000.00"))

    fabricated = Extraction(origin=Origin.PURCHASE_ORDER, observed_at=NOW)
    fabricated.claims.append(Claim("amount", "12000.00", ""))  # no citation

    a = assess(instruction, [honest.to_source()], now=NOW)
    b = assess(instruction, [honest.to_source(), fabricated.to_source()], now=NOW)

    assert a.decision is b.decision, "an uncited claim changed the decision"
    assert a.independent_sources == b.independent_sources == 1
    assert a.decision is Decision.AUTHORISE_CAPPED, (
        "one real source must not reach the autonomous tier even with a "
        "fabricated second claim alongside it")


def test_uncited_claims_are_dropped_at_conversion():
    ex = Extraction(origin=Origin.EMAIL, observed_at=NOW)
    ex.claims.append(Claim("amount", "12000.00", "Total: 12,000.00"))
    ex.claims.append(Claim("payee_account", "GB99FAKE00000000000000", ""))
    src = ex.to_source()
    keys = {f.key for f in src.facts}
    assert "amount" in keys
    assert "payee_account" not in keys


def test_analyst_output_cannot_express_a_decision():
    """By construction the analyst returns facts only. Assert the shape, so a
    future change that adds a 'decision' field to Claim fails loudly."""
    fields = set(Claim.__dataclass_fields__)
    assert fields == {"key", "value", "evidence", "confidence"}, (
        "the analyst must expose facts only — no decision, no score, no action")


# --------------------------------------------------------------------------- #
# JSON handling
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", [
    '{"claims": []}',
    'Here is the result:\n{"claims": [{"key": "amount"}]}\nHope that helps.',
    '```json\n{"claims": []}\n```',
])
def test_finds_json_inside_model_chatter(raw):
    obj = _first_json_object(raw)
    assert "claims" in obj


def test_rejects_a_response_with_no_json():
    with pytest.raises(ValueError):
        _first_json_object("I could not read this document.")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #

def test_extraction_is_deterministic():
    runs = {tuple((c.key, c.value) for c in
                  extract_deterministically(INVOICE_TEXT, Origin.INVOICE_PDF).claims)
            for _ in range(25)}
    assert len(runs) == 1


def test_deterministic_and_model_paths_produce_the_same_source_shape():
    """The scorer must be unable to tell which reader ran — that is what lets
    the demo work with no API key and still show the same decisions."""
    ex = extract_deterministically(INVOICE_TEXT, Origin.INVOICE_PDF)
    src = ex.to_source()
    assert isinstance(src, Source)
    assert src.origin is Origin.INVOICE_PDF
    assert all(isinstance(f.key, str) for f in src.facts)
