"""The analyst layer — the ONLY place a model's reading is allowed to enter.

THE DIVISION OF LABOUR, STATED PRECISELY
The brief for this event: "the model reads, the code decides."

So this module does exactly one job: turn unstructured documents into CLAIMS
with citations. It extracts:

    - which facts a document asserts (amount, payee, reference)
    - the verbatim sentence each fact came from

It never decides anything. It never computes a score, a tier, or a cap. It
cannot refuse or authorise a payment. Its entire output is a list of claims
that `src/evidence/score.py` then judges in code.

WHY THIS MATTERS FOR THIS PRODUCT SPECIFICALLY
A module that decides how much money an agent may move must not be promptable.
If the analyst could return "this looks fine, pay it", then a manipulated email
could talk the agent into a larger transfer. By contract, the analyst's output
is FACTS ONLY: the worst a compromised analyst can do is report a false fact,
which the scorer then weighs against other sources rather than trusting.

WHY THERE IS A DETERMINISTIC FALLBACK
Two reasons, both practical:
  - the demo must run with no API key and no network, and it must produce the
    same decisions as the keyed path
  - a judge can read the fallback and the model path side by side and confirm
    that the numbers never come from the model

With a key, the model is asked to extract with citations. Without one, a regex
extractor does the same job. Both produce the same `Extraction` shape, so the
scorer cannot tell which was used — which is the point.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.evidence.score import Fact, Origin, Source


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #

@dataclass
class Claim:
    """A fact, the exact text it came from, and the model's confidence in it.

    `evidence` is mandatory: a claim with no quoted source is discarded rather
    than trusted. That single rule is what stops a model inventing an amount,
    because an invention has nothing to quote.
    """
    key: str
    value: str
    evidence: str
    confidence: float = 1.0


@dataclass
class Extraction:
    origin: Origin
    claims: list[Claim] = field(default_factory=list)
    observed_at: datetime | None = None
    label: str = ""
    method: str = "deterministic"     # "model" or "deterministic" — for the log
    dropped: list[str] = field(default_factory=list)

    def to_source(self, declared_verifiable: bool = True) -> Source:
        """Convert to the scorer's input, dropping uncited claims."""
        facts = tuple(
            Fact(c.key, c.value) for c in self.claims if c.evidence.strip()
        )
        return Source(
            origin=self.origin,
            facts=facts,
            observed_at=self.observed_at or datetime.now(timezone.utc),
            verifiable=declared_verifiable,
            label=self.label,
        )


# --------------------------------------------------------------------------- #
# Deterministic extractor — the fallback, and the reference behaviour
# --------------------------------------------------------------------------- #

# Currency amounts. Deliberately permissive about separators and symbols, since
# the value is compared numerically downstream and the raw text is kept as
# evidence either way.
_AMOUNT_RE = re.compile(
    r"(?:(USD|EUR|GBP|AUD|SGD|\$|€|£)\s*)?"
    r"(\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)

# IBAN-ish account identifiers. Shape check only — full validation belongs to
# the payment rail, not the reader.
_ACCOUNT_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")

# Invoice / reference labels.
_REFERENCE_RE = re.compile(
    r"(?:invoice|inv|reference|ref|po|order)\s*(?:no\.?|number|#|:)?\s*"
    r"([A-Z]{2,4}[-–]?\d{2,10})",
    re.IGNORECASE,
)

# An amount sitting next to one of these words is much more likely to be the
# total than a rate, a tax figure or a line item.
#
# THE BOUNDARY MATTERS. An earlier version matched bare `total`, which also
# matches "Subtotal" — so the extractor picked the SUBTOTAL as the amount
# payable, and the demo would have paid 11,000 instead of 12,000. A test
# asserts the total wins over subtotal and VAT. Keep the word boundaries and
# the explicit subtotal exclusion.
_TOTAL_CONTEXT = re.compile(
    r"(?<!sub)\btotal\b|\bamount\s+due\b|\bbalance\s+due\b|\bsum\s+due\b"
    r"|\bpayable\b|\bplease\s+pay\b",
    re.IGNORECASE,
)
# Figures that are never the amount payable. If a sentence matches one of these
# it is describing a component, not the total.
_COMPONENT_CONTEXT = re.compile(
    r"\bsubtotal\b|\bvat\b|\btax\b|\bnet\s+of\b|\brate\b|\bper\s+(?:unit|hour|day)\b"
    r"|\bdiscount\b|\bshipping\b|\bfee\b",
    re.IGNORECASE,
)

# Identifiers that contain digits but are not money. Without this, "PO-88214"
# is read as an amount of 88214 — which is not a rounding error, it is a
# fabricated figure. A five-digit reference defeated a length-only guard.
_IDENTIFIER_PREFIX = re.compile(
    r"(?:PO|INV|REF|ORD|SO|DN|CN|NO|ACCT|ACC|IBAN|VAT|TAX|CUST|TKT)"
    r"[-–_ ]?\s*$",
    re.IGNORECASE,
)


def _is_money_like(number: str, symbol: str | None, sentence: str) -> bool:
    """Is this digit run plausibly an amount of money?

    Three guards, all necessary:
      - a currency symbol makes it money regardless of separators
      - proper money has a decimal part or a thousands separator, so a bare
        integer run is treated with suspicion
      - a digit run immediately preceded by an identifier prefix (PO-, INV-,
        REF-) is a reference number, never an amount
    """
    if symbol:
        return True
    stripped = number.strip()
    # Find the digit run's position and look at what precedes it.
    idx = sentence.find(stripped)
    if idx > 0:
        before = sentence[:idx]
        if _IDENTIFIER_PREFIX.search(before):
            return False
    # Bare integers with no separators are usually quantities or identifiers.
    return "." in stripped or "," in stripped or len(stripped) >= 6


def _sentences(text: str) -> list[str]:
    """Split into sentences, keeping them verbatim for citation."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_deterministically(text: str, origin: Origin,
                              label: str = "") -> Extraction:
    """Pull facts out with patterns. No model, no network, fully reproducible."""
    ex = Extraction(origin=origin, label=label, method="deterministic")
    sentences = _sentences(text)

    # --- amount: prefer a sentence that says "total"; never a component ---
    #
    # Two tiers, and the order matters. A sentence naming the total wins. A
    # sentence naming a COMPONENT (subtotal, VAT, a rate) is disqualified
    # outright rather than merely deprioritised, because paying the subtotal or
    # the tax figure is the most expensive reading error available here.
    total_pick: tuple[str, str] | None = None
    neutral_pick: tuple[str, str] | None = None
    for sent in sentences:
        is_component = bool(_COMPONENT_CONTEXT.search(sent))
        is_total = bool(_TOTAL_CONTEXT.search(sent))
        for m in _AMOUNT_RE.finditer(sent):
            symbol, number = m.group(1), m.group(2)
            # A digit run may be a reference number, a quantity or a rate.
            # Paying one of those as the amount is a fabricated figure, not a
            # rounding error, so the guard is explicit rather than a length test.
            if not _is_money_like(number, symbol, sent):
                continue
            value = f"{symbol} {number}".strip() if symbol else number
            if is_total and total_pick is None:
                total_pick = (value, sent)
            elif not is_component and neutral_pick is None:
                neutral_pick = (value, sent)
    chosen = total_pick or neutral_pick
    if chosen:
        ex.claims.append(Claim("amount", chosen[0], chosen[1]))
    else:
        ex.dropped.append("amount")

    # --- payee account ---
    for sent in sentences:
        m = _ACCOUNT_RE.search(sent)
        if m:
            ex.claims.append(Claim("payee_account", m.group(1), sent))
            break
    else:
        ex.dropped.append("payee_account")

    # --- reference ---
    for sent in sentences:
        m = _REFERENCE_RE.search(sent)
        if m:
            ex.claims.append(Claim("reference", m.group(1).upper(), sent))
            break
    else:
        ex.dropped.append("reference")

    return ex


# --------------------------------------------------------------------------- #
# Model extractor
# --------------------------------------------------------------------------- #

PROMPT = """You extract facts from a financial document. You do not decide anything.

Return ONLY a JSON object with this exact shape:

{"claims": [{"key": "amount"|"payee_account"|"reference",
             "value": "<the value, as text>",
             "evidence": "<the exact sentence you took it from>",
             "confidence": 0.0-1.0}]}

RULES
- `evidence` MUST be copied verbatim from the document. A claim you cannot
  quote must be omitted, not guessed.
- Report the TOTAL amount payable, not line items, tax, or unit rates.
- Do not invent a value to fill a key. Omit the key instead.
- Do not comment, explain, or add any other keys.

DOCUMENT
--------
%s
--------
"""


def extract_with_model(text: str, origin: Origin, label: str = "",
                       model: str | None = None) -> Extraction:
    """Ask a model to extract claims. Falls back on ANY failure.

    The fallback is not a nicety: the demo must produce identical decisions
    whether or not a key is configured, so an API error cannot change what the
    agent is allowed to pay.
    """
    ex = Extraction(origin=origin, label=label, method="model")
    try:
        raw = _call_model(PROMPT % text, model=model)
        payload = _first_json_object(raw)
        for item in payload.get("claims", []):
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            evidence = str(item.get("evidence", "")).strip()
            if not key or not value:
                ex.dropped.append(f"{key or '(no key)'} (empty)")
                continue
            if not evidence:
                # No quote means no support. Discard rather than trust.
                ex.dropped.append(f"{key} (no citation)")
                continue
            if key not in {"amount", "payee_account", "reference"}:
                ex.dropped.append(f"{key} (unexpected key)")
                continue
            try:
                conf = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                conf = 1.0
            ex.claims.append(Claim(key, value, evidence, conf))
        if not ex.claims:
            raise RuntimeError("model returned no usable claims")
        return ex
    except Exception as exc:                      # noqa: BLE001
        fallback = extract_deterministically(text, origin, label)
        fallback.dropped.append(f"model path failed: {type(exc).__name__}")
        fallback.method = "deterministic (model unavailable)"
        return fallback


def _first_json_object(raw: str) -> dict:
    """Find the first complete JSON object in a model's reply.

    Models wrap JSON in prose or fences often enough that demanding a clean
    document would make the keyed path useless in practice.
    """
    depth, start = 0, None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(raw[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    start = None
    raise ValueError("no JSON object in model response")


def _call_model(prompt: str, model: str | None = None) -> str:
    """Minimal OpenAI-compatible call. Any provider works; the key decides."""
    import urllib.request

    key = os.environ.get("ATTEST_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("no LLM key configured")
    base = os.environ.get("ATTEST_LLM_BASE", "https://api.openai.com/v1")
    name = model or os.environ.get("ATTEST_LLM_MODEL", "gpt-4o-mini")

    body = json.dumps({
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def extract(text: str, origin: Origin, label: str = "",
            use_model: bool | None = None) -> Extraction:
    """Extract claims from one document.

    `use_model=None` means "use a model if a key is available, otherwise the
    deterministic reader". The 18-kit starter this event ships does the same
    thing, and the demo says so out loud.
    """
    if use_model is None:
        use_model = bool(os.environ.get("ATTEST_LLM_KEY")
                         or os.environ.get("OPENAI_API_KEY"))
    if use_model:
        return extract_with_model(text, origin, label)
    return extract_deterministically(text, origin, label)
