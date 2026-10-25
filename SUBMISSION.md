# ATTEST — Submission Text

**Hackathon:** Airwallex Agentic Banking Hackathon (HackerEarth)
**Track:** Starter Kit 01 — Adaptive Treasury Controller
**Team:** Solo (Hussein Adeiza Yunusa — @web3alphatester)

---

## Short description (form field, ~150 words)

ATTEST gates an agent's spending authority on the **quality of the evidence**
behind a payment instruction — not just the balance. Eleven thousand dollars is
identical whether it arrives as an invoice backed by a matching purchase order,
or as an anonymous email marked "urgent, do not call to confirm." One deserves
autonomous payment; the other deserves nothing.

The model reads documents and extracts claims **with citations**. Pure code
scores the evidence and decides. The gate — the only code that touches money —
cannot act on a malformed assessment, and every decision, including refusals,
lands in a ledger. One identical $12,000 instruction produces four outcomes:
verified pair pays in full, single source pays 25% capped, contradicted sources
escalate to a human, an unverifiable source is refused and recorded.

Stack: Python + FastAPI-free stdlib core; mock rail (live) and Airwallex
sandbox client on the same interface.

---

## Long description

### The problem

The brief's grading criterion is one sentence:

> "Does an agent make a financial decision that changes when the balance,
> deadline, **evidence** or **risk** changes — and move money with it?"

Twelve starter kits cover balance, deadline and risk. **None makes the decision
change on evidence integrity itself.** Kit 15 validates bank details; Kit 11
matches a receipt — but no kit asks whether the agent should trust its own
inputs. That gap is ATTEST.

### The mechanism

Three layers, strictly separated — *the model reads, the code decides, the API
moves the money*:

1. **Analyst** (`src/analyst/extract.py`) — extracts claims with verbatim
   citations from each document. A claim with **no citation is discarded**;
   a test proves a fabricated source cannot change a decision.
2. **Evidence scorer** (`src/evidence/score.py`) — pure code, no model, no
   clock, no network. Four tiers derived independently in this submission:
   two agreeing origins → AUTHORISE; single source → AUTHORISE_CAPPED (25% of
   requested); contradicting sources → ESCALATE; unverifiable origin → REFUSE.
3. **Gate** (`src/gate/execute.py`) — the only code that calls the rail.
   It **raises** on a malformed assessment rather than guessing. A verified
   property test (monotonicity, scale invariance, source independence, boundary
   symmetry) holds for any correct implementation of the design, not just this
   one.

### Verification — the part most submissions assert and this one proves

- **75 tests**, including 12 falsification tests written against independently
  derived properties of the design, so they could actually fail.
- `docs/evidence.md` maps **every claim in the README to the artifact that
  proves it**, with a checker (`docs/verify_evidence.py`) that runs the repo
  and asserts the document: 29 checks, 0 failures.
- Four real bugs found and pinned during development, including a purchase
  order's **number being extracted as its amount** (88,214 instead of 19,000)
  — the exact class of error that turns an agent into a liability.
- **The demo changes outcomes when the evidence changes.** Editing one field in
  one evidence file flips Case C from ESCALATE to AUTHORISE. That single
  comparison is the product.

### Honest limitations (stated, not buried)

1. **The demo runs on the mock rail.** The Airwallex sandbox client exists on
   the same interface and the correct auth shape was verified against
   Airwallex's reference, but no live sandbox call has completed. **This is the
   submission's weakest line and we are stating it first.**
2. Provenance is structural, not cryptographic — two forged documents that
   agree still corroborate.
3. "Independent origin" means different declared origin, not different owner.
4. Thresholds (25% cap, 1% tolerance, 30-day staleness) are stated judgements,
   in one block, reviewable in seconds.
5. The extractor is tuned to invoice-shaped text, proven over our sample set.
6. No external audit.

### What a judge should run first

```bash
ATTEST_PACE=0 python3 -m src.demo.run_demo   # four cases, ~0s, no credentials
python3 -m pytest tests/ -q                  # 75 tests
python3 docs/verify_evidence.py              # 29 checks on this text's claims
```

The demo needs **no credentials and no network**. The refusal paths, cap
logic and escalation reasons are all verifiable offline; the sandbox client
swaps in behind the same interface.

### Fit to Track 1

Adaptive Treasury Controller: *"Decide what to fund, convert, defer, or
escalate when cash is short."* ATTEST answers the adjacent question the kit
leaves open — **before** deciding what to fund, decide **whether the evidence
justifies funding it at all**, and make every one of those decisions visible,
reversible, and recorded. The escrow-style cap (25%) and the recorded refusals
give a treasury ops team the missing control surface between "the agent pays
everything" and "a human approves everything."

---

## Video script notes (if re-recorded, <5 min required — current cut is 59s)

- 0:00 — the same invoice twice; different decisions
- 0:08 — the three layers and the no-citation rule
- 0:20 — the four cases, on screen, as they run
- 0:35 — the falsification property table
- 0:45 — limitations, stated on screen, then next steps
