# ATTEST

**An agent's authority to move money scales with how well-evidenced the instruction is — not only with the balance.**

Airwallex Agentic Banking Hackathon · Treasury domain

---

## The problem, in one paragraph

An accounts-payable agent that can pay anything is a liability. An agent that
pays nothing without a human is not an agent. The usual fix is a spending limit:
a cap on the amount, or a list of approved payees. That treats every 12,000 USD
invoice as equally risky, which is exactly wrong — the amount is identical
whether the instruction arrived on letterhead with a matching purchase order or
in an anonymous email marked "urgent, do not call to confirm."

The risk is not in the amount. **It is in the evidence behind it.**

## What ATTEST does

Every payment instruction is accompanied by evidence: an invoice, a purchase
order, a contract, an email. ATTEST reads that evidence, scores it in code, and
sets the agent's authority accordingly.

| Evidence | The agent's authority |
|---|---|
| Two independent sources agree | **Pay in full, no human** |
| One source, well formed | **Pay, capped at 25%** |
| Sources contradict each other | **Escalate to a human** |
| Nothing verifiable | **Refuse, and record the refusal** |

**The same 12,000 USD instruction produces all four outcomes**, decided only by
the evidence behind it. That single comparison is the product.

---

## The demo video

`video/out/attest-demo-narrated.mp4` — 59 seconds, recorded from a real terminal
running the command below, not from motion graphics. The product is a decision
trail; the evidence is the decisions and the ledger that records them.

**Disclosures about that video, stated here rather than left for a judge to
notice:**

- **The narration is synthesised** (OpenAI `tts-1-hd`, "onyx"). It is not the
  author's voice. The voice describes what is on screen at that moment.
- **The rail shown is the mock**, and the video says so on screen — see
  limitation 1 below for why.
- **The captions and narration are timed to beats measured from the recording**
  (pixel changes, cross-checked against the demo's own timestamps), so no
  caption claims anything the footage does not show at that instant.

## The demo

```bash
python3 -m src.demo.run_demo
```

**Do X:** run the command. No credentials, no network, no setup.
**See Y:** four cases, one identical instruction, four different decisions, and
a ledger showing every one — including the refusals.

The run is paced for screen recording by default; set `ATTEST_PACE=0` for the
instant version the tests use.

A sample run:

```
CASE A  invoice + purchase order, both 12,000   -> AUTHORISE        PAID 12,000.00
CASE B  invoice alone                           -> AUTHORISE_CAPPED PAID  3,000.00
CASE C  invoice 12,000 vs PO 19,000             -> ESCALATE         PAID      0.00
CASE D  unverifiable email                      -> REFUSE           PAID      0.00
```

**To verify the mechanism rather than take it on faith:** edit any file in
`evidence_samples/` and re-run. Change the purchase order's total to match, and
Case C pays in full. Delete it, and Case A drops to the capped tier. The
outcome follows the evidence, not a script.

```bash
python3 -m pytest tests/ -q      # 63 tests
```

---

## Why the numbers cannot come from a model

The brief for this event: *"the model reads, the code decides."* That discipline
matters most in the layer that authorises money, so ATTEST splits the work
strictly:

```
analyst    reads documents        -> claims + verbatim citations   (may be a model)
evidence   scores the claims      -> assessment                    (pure code)
gate       acts on the assessment -> payment or escalation         (pure code)
```

**The analyst cannot decide anything.** Its output type is
`{key, value, evidence, confidence}` and nothing else — a test asserts that
field set exactly, so adding a decision field later fails loudly.

**A claim with no citation is discarded.** That is the rule that stops a
hallucination becoming a payment: an invented figure has nothing to quote. A
test simulates a model returning a fabricated source with no citation and
asserts the decision is **unchanged** — two bugs were found this way, including
one where an uncited source still counted as corroboration and could have
manufactured authority to pay in full.

**The gate refuses to act on a bad assessment** rather than guessing: no
assessment, a capped decision with no cap, a cap above the amount, or a negative
cap all raise.

---

## The four guards that keep a fabricated figure out

Amounts are where a reading error becomes a wrong payment, so the extractor
applies three explicit checks rather than trusting a pattern match:

1. a currency symbol means money, regardless of formatting
2. proper money carries a decimal part or a thousands separator
3. a digit run preceded by `PO-`, `INV-`, `REF-`, `ORDER NO:` is an identifier

Plus a component guard: sentences naming a **subtotal, VAT, tax, rate, fee or
discount** are disqualified outright, not merely deprioritised.

**Both guards exist because both bugs happened.** `"Subtotal"` matched a bare
`total` pattern, so the extractor read 11,000 instead of 12,000. And `PO-88214`
was read as an amount of **88,214** — a figure the document does not contain.
A test now asserts that every extracted figure appears verbatim in its source.

---

## Layout

```
src/evidence/score.py     the pure scorer — no model, no I/O, no clock
src/analyst/extract.py    claims with citations only; cannot decide anything
src/gate/execute.py       the only code that moves money
src/rails/payment.py      the mock rail, and the Airwallex sandbox client
src/demo/run_demo.py      one invoice, four evidence profiles
evidence_samples/         the documents the demo reads
tests/                    63 tests
```

---

## Adoption thesis

- **Actor who must act:** the finance team that has given, or is considering
  giving, an AP agent real payment authority.
- **Their gain:** autonomy on strong evidence, without accepting unbounded risk
  on weak evidence. Today the only settings are "the agent pays everything" or
  "a human approves everything."
- **Their loss:** some payments that would have proceeded autonomously now need
  approval, and some are paid in part rather than in full. That is the product,
  not a defect — but it is a real cost and it is stated here rather than buried.
- **Who feels the pain:** the same finance team. A wrong payment is their loss
  and their audit finding.
- **Same actor?** **Yes.** The party who benefits, the party who bears the risk,
  and the party who can act are one and the same. Nothing has to change for a
  third party, and there is no incumbent whose cooperation is required.
- **Why they would adopt:** the alternative is either unbounded authority or no
  autonomy. The cost of adoption is a policy document and an evidence pipeline,
  against a class of loss that is otherwise uninsurable.

---

## Honest limitations

**1. The demo runs on the mock rail, and says so.** The output prints
`rail: mock`. The Airwallex sandbox client exists and implements the same
interface, but it has **never been executed** — no credentials exist on this
machine. It must be run against the sandbox before any claim of a live payment.
Until then, every payment shown is simulated.

**2. The evidence score depends on provenance we do not cryptographically
verify.** ATTEST trusts a caller to declare that a source is authentic and
distinct. It detects *structural* weakness — an uncorroborated claim, a
contradiction between sources, an unverifiable origin — not a well-forged
document that agrees with a forged purchase order. A determined attacker who
controls two origins can still corroborate a false instruction. Closing that
needs signed documents or an out-of-band confirmation channel, which is not
built here.

**3. "Independent origin" is a coarse proxy.** Two sources count as independent
when their declared origins differ. An invoice and a purchase order from the
same compromised mailbox are treated as corroborating evidence. Real
independence needs an ownership graph, not an enum.

**4. The thresholds are judgements, not findings.** 25% for the capped tier, 30
days for staleness, 1% for amount tolerance. They are visible and reviewable in
one block at the top of `score.py`, and they are set to be defensible rather
than fitted to data — there is no labelled corpus to fit them to.

**5. The extractor is tuned to the shapes of the sample documents.** It reads
totals, IBANs and references reliably from invoice-like text. It is not a
general document parser, and a scanned PDF or an unusual layout would need the
model path (which falls back to these patterns on any failure).

**6. Not audited.** Testnet-grade code written for a hackathon.

---

## What would come next

- **Signed evidence.** Have each source sign its claims, so provenance is
  checked rather than declared. That removes limitation 2.
- **An ownership graph** for real source independence, removing limitation 3.
- **Calibrate the thresholds** against real AP data rather than judgement.
- **Partial payment as a first-class outcome.** The capped tier currently pays a
  fraction; in practice a finance team may want to pay a verified component and
  hold the rest.

## Licence

MIT.
