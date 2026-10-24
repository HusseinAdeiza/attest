"""Evidence scoring — the part of ATTEST that a model is not allowed to touch.

THE THESIS
An agent's authority to move money should scale with how well-evidenced the
instruction is, not only with the balance. A payment backed by corroborating,
independent, current sources can proceed autonomously. The same payment backed
by a single source, a stale document, or contradictory evidence cannot.

WHY THIS IS PURE CODE
The brief for this event says "the model reads, the code decides." That rule
matters more here than anywhere else, because this module decides how much
money an agent may move. A model can be prompted, jailbroken, or simply wrong;
a threshold cannot. The analyst layer extracts CLAIMS with citations and hands
them here as plain data. Everything numeric — scores, tiers, caps — is computed
in this file and nowhere else.

WHAT "EVIDENCE" MEANS PRECISELY
An instruction (pay X to Y) is accompanied by one or more SOURCES. Each source
asserts a set of FACTS about the instruction. Two sources are INDEPENDENT when
they do not share an origin, because two documents from the same origin are one
document repeated.

The score answers one question: how much corroboration does this instruction
have, and is anything contradicting it?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

class Origin(str, Enum):
    """Where a source came from.

    Two sources with the same origin are NOT independent: a supplier who sends
    the same invoice by email and by portal has sent one document twice.
    """
    INVOICE_PDF = "invoice_pdf"
    EMAIL = "email"
    CONTRACT = "contract"
    PURCHASE_ORDER = "purchase_order"
    APPROVAL_SYSTEM = "approval_system"
    BANK_FEED = "bank_feed"
    ERP_RECORD = "erp_record"
    MESSAGE = "message"
    UNKNOWN = "unknown"


class Decision(str, Enum):
    """The authority tier. Ordered from most to least autonomous."""
    AUTHORISE = "AUTHORISE"              # pay in full, no human
    AUTHORISE_CAPPED = "AUTHORISE_CAPPED"  # pay, but only up to a reduced cap
    ESCALATE = "ESCALATE"                # a human must approve
    REFUSE = "REFUSE"                    # do not pay; recorded


class Reason(str, Enum):
    """Stable, machine-readable justification. Every decision carries one.

    A refusal with no reason is indistinguishable from a bug, and an audit
    trail that cannot explain itself is not an audit trail.
    """
    CORROBORATED = "CORROBORATED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    CONTRADICTED = "CONTRADICTED"
    STALE = "STALE"
    NO_EVIDENCE = "NO_EVIDENCE"
    UNVERIFIABLE_ORIGIN = "UNVERIFIABLE_ORIGIN"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Fact:
    """One thing a source asserts about the instruction.

    Values are normalised to strings before comparison so that "12,000.00",
    "12000" and "12000.0000" cannot produce a spurious contradiction. Amounts
    arrive already canonicalised by the analyst layer, which is the only place
    a model's reading is allowed to enter — and it enters as text, not numbers.
    """
    key: str          # e.g. "amount", "payee_account", "reference"
    value: str


@dataclass(frozen=True)
class Source:
    """A document or record offered in support of the instruction."""
    origin: Origin
    facts: tuple[Fact, ...]
    observed_at: datetime
    # A caller may declare a source unverifiable (e.g. an anonymous email).
    verifiable: bool = True
    # Free-text label for the audit trail, never used in scoring.
    label: str = ""

    def value_of(self, key: str) -> str | None:
        for f in self.facts:
            if f.key == key:
                return f.value
        return None

    def keys(self) -> set[str]:
        return {f.key for f in self.facts}


@dataclass(frozen=True)
class Instruction:
    """The payment the agent has been asked to make."""
    amount: Decimal
    currency: str
    payee_name: str
    payee_account: str
    reference: str = ""


# --------------------------------------------------------------------------- #
# Tunables — every threshold lives here, visible and reviewable
# --------------------------------------------------------------------------- #

# A source older than this is stale. A six-month-old invoice is not evidence
# that money is owed today.
MAX_EVIDENCE_AGE = timedelta(days=30)

# Amounts differing by more than this fraction are a contradiction, not a
# rounding difference. 1% tolerates FX and rounding; it does not tolerate
# somebody editing a digit.
AMOUNT_TOLERANCE = Decimal("0.01")

# The cap applied when evidence is real but uncorroborated, as a fraction of
# the requested amount. An uncorroborated instruction can still be paid, but
# never in full — so a single forged document cannot authorise a large transfer.
SINGLE_SOURCE_CAP_FRACTION = Decimal("0.25")

# The score at or above which the agent may act without a human. Below it, a
# person decides.
#
# THIS MUST SIT BELOW THE SINGLE-SOURCE SCORE, or the capped tier is
# unreachable and a lone document escalates instead of paying a reduced amount.
# An earlier version set this to 0.50 while a single source scored 0.40, so
# AUTHORISE_CAPPED could never fire — the tier the whole product is built
# around was dead code. The tests caught it; keep the ordering invariant true.
ESCALATE_BELOW = Decimal("0.35")


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

@dataclass
class Assessment:
    decision: Decision
    reason: Reason
    score: Decimal
    cap: Decimal | None
    independent_sources: int
    total_sources: int
    detail: list[str] = field(default_factory=list)

    def explain(self) -> str:
        lines = [f"  decision : {self.decision.value}  ({self.reason.value})",
                 f"  score    : {self.score}",
                 f"  sources  : {self.independent_sources} independent "
                 f"of {self.total_sources} offered"]
        if self.cap is not None:
            lines.append(f"  cap      : {self.cap}")
        for d in self.detail:
            lines.append(f"  · {d}")
        return "\n".join(lines)


def _canonical_amount(raw: str) -> Decimal | None:
    """Parse an amount string without letting float error in.

    Returns None when the text is not a number at all — which is a different
    condition from a number that disagrees, and must be reported as such.
    """
    cleaned = raw.replace(",", "").replace(" ", "")
    for prefix in ("USD", "EUR", "GBP", "$", "€", "£"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    try:
        return Decimal(cleaned)
    except Exception:                              # noqa: BLE001
        return None


def assess(instruction: Instruction, sources: list[Source],
           now: datetime | None = None) -> Assessment:
    """Score the evidence and return the authority tier.

    Pure: no clock reads beyond the caller-supplied `now`, no I/O, no model.
    That is what makes the behaviour testable and the thresholds auditable.
    """
    now = now or datetime.now(timezone.utc)
    detail: list[str] = []

    usable = [s for s in sources if s.verifiable]
    unverifiable = len(sources) - len(usable)
    if unverifiable:
        detail.append(f"{unverifiable} source(s) unverifiable — ignored")

    if not usable:
        return Assessment(Decision.REFUSE, Reason.NO_EVIDENCE if not sources
                          else Reason.UNVERIFIABLE_ORIGIN,
                          Decimal("0"), None, 0, len(sources),
                          detail + ["no usable evidence was offered"])

    total = len(usable)

    # 1. Freshness. A stale source is not evidence about today.
    fresh = [s for s in usable
             if now - s.observed_at <= MAX_EVIDENCE_AGE]
    stale = total - len(fresh)
    if stale:
        detail.append(f"{stale} source(s) older than {MAX_EVIDENCE_AGE.days} days")
    if not fresh:
        return Assessment(Decision.REFUSE, Reason.STALE, Decimal("0"), None,
                          0, total, detail + ["every source is out of date"])

    # 2. Independence. Two sources sharing an origin corroborate nothing; they
    #    are one document presented twice.
    #
    #    A source that asserts NOTHING is not evidence either. This check is
    #    load-bearing for the product's central claim: without it, a reader
    #    that returns an empty or fully-uncited document still registers as an
    #    independent origin, and a compromised analyst could manufacture the
    #    corroboration needed to authorise a payment. A test asserts that a
    #    fabricated second source cannot change the decision.
    empty = [s for s in fresh if not s.facts]
    if empty:
        detail.append(f"{len(empty)} source(s) assert no facts — not counted")
    fresh = [s for s in fresh if s.facts]
    if not fresh:
        return Assessment(Decision.REFUSE, Reason.NO_EVIDENCE, Decimal("0"),
                          None, 0, total,
                          detail + ["every source was unreadable or asserted nothing"])

    origins = {s.origin for s in fresh}
    independent = len(origins)
    if independent < len(fresh):
        detail.append(f"{len(fresh) - independent} source(s) share an origin "
                      f"with another — not independent")

    # 3. Contradiction on the amount. This is the check that catches a tampered
    #    document, so it runs before any corroboration credit is given.
    claimed: list[Decimal] = []
    for s in fresh:
        raw = s.value_of("amount")
        if raw is None:
            continue
        val = _canonical_amount(raw)
        if val is None:
            detail.append(f"unparseable amount in {s.origin.value}: {raw!r}")
            continue
        claimed.append(val)

    if claimed:
        lo, hi = min(claimed), max(claimed)
        spread = hi - lo
        # Tolerance is proportional to the larger figure, so it behaves the same
        # for a $50 invoice and a $50,000 one.
        allowed = (hi * AMOUNT_TOLERANCE) if hi else Decimal("0")
        if spread > allowed:
            detail.append(f"sources disagree on amount: {lo} vs {hi}")
            return Assessment(Decision.ESCALATE, Reason.CONTRADICTED,
                              Decimal("0.25"), None, independent, total, detail)

    # 4. Corroboration score.
    #
    #    One source can never be enough to authorise in full — that is the
    #    whole point of the product. Each additional INDEPENDENT origin adds
    #    weight, with diminishing returns so that five near-duplicate sources
    #    do not outrank two genuinely separate ones.
    if independent <= 1:
        score = Decimal("0.40")
        reason = Reason.SINGLE_SOURCE
    elif independent == 2:
        score = Decimal("0.75")
        reason = Reason.CORROBORATED
    else:
        score = Decimal("0.90")
        reason = Reason.CORROBORATED
    detail.append(f"corroboration from {independent} independent origin(s)")

    # 5. Tier. The cap on the middle tier is what turns a forged single
    #    document from a large loss into a small one.
    if score >= ESCALATE_BELOW and independent >= 2:
        return Assessment(Decision.AUTHORISE, reason, score, None,
                          independent, total, detail)
    if score >= ESCALATE_BELOW:
        cap = (instruction.amount * SINGLE_SOURCE_CAP_FRACTION).quantize(
            Decimal("0.01"))
        detail.append(f"uncorroborated: capped at "
                      f"{SINGLE_SOURCE_CAP_FRACTION * 100:.0f}% of requested")
        return Assessment(Decision.AUTHORISE_CAPPED, reason, score, cap,
                          independent, total, detail)
    return Assessment(Decision.ESCALATE, reason, score, None,
                      independent, total, detail)
