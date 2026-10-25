# Evidence

Every claim in the README, and the artifact that proves it. Where a claim cannot
be verified from this repository, it is listed under **Not verified** rather than
softened.

**Commit:** `8757e62`
**Method:** every number below is read from a stored run artifact in
`docs/evidence/` or produced by a command in this file. Nothing is transcribed
from memory.

To reproduce everything:

```bash
python3 -m pytest tests/ -q                    # 63 tests
ATTEST_PACE=0 python3 -m src.demo.run_demo     # the four-case run
```

---

## The core claim: one instruction, four decisions

> *The instruction never changes. The evidence does.*

**Verify:** `ATTEST_PACE=0 python3 -m src.demo.run_demo`
**Stored output:** `docs/evidence/demo-run.txt` (61 lines)

| Case | Evidence given | Decision | Money moved |
|---|---|---|---|
| A | invoice **and** purchase order, both 12,000 | `AUTHORISE` (CORROBORATED) | **12,000.00** |
| B | invoice alone | `AUTHORISE_CAPPED` (SINGLE_SOURCE) | **3,000.00** |
| C | invoice 12,000 vs PO 19,000 | `ESCALATE` (CONTRADICTED) | **0** |
| D | email, unverifiable origin | `REFUSE` (UNVERIFIABLE_ORIGIN) | **0** |

The instruction block is printed once at the top and is identical for all four
cases: `pay 12000.00 USD to Meridian Freight Ltd`, account
`GB29NWBK60161331926819`, reference `INV-4471`.

**Ledger totals from the same run:**

```
paid             15000.00 USD
refused          12000.00 USD (no payment attempted)
escalated        12000.00 USD (awaiting a human)
withheld by cap   9000.00 USD (paid in part because evidence was thin)
```

---

## The claim that the demo is not scripted

> *Change the evidence and the outcome changes.*

**Verify:** edit `evidence_samples/purchase_order_mismatch.txt`, changing
`USD 19,000.00` back to `USD 12,000.00`, and re-run. **Case C stops escalating
and pays in full** — because the two documents now agree. Restore the file and
it escalates again.

This was tested during development, and it also caught a real bug: while
demonstrating it, the fixture was left mutated, and the test suite caught it —
`test_the_two_purchase_orders_differ_by_exactly_the_total` fired because the two
purchase orders had become identical, which would have silently collapsed the
Case A / Case C distinction.

---

## Test evidence

**Verify:** `python3 -m pytest tests/ -q`
**Stored output:** `docs/evidence/tests-run.txt`

```
75 passed
```

| File | Tests | What it covers |
|---|---|---|
| `tests/test_evidence_score.py` | 21 | the scorer: corroboration, contradiction, staleness, thresholds |
| `tests/test_extractor_regression.py` | 15 | the amount-extraction bugs that shipped, pinned |
| `tests/test_gate.py` | 14 | all four outcomes, and the four cases where the gate must REFUSE to act |
| `tests/test_analyst_extract.py` | 13 | citation discipline — a claim with no citation is discarded |
| `tests/test_falsification.py` | 12 | **properties that would prove the scorer WRONG** — see below |

### The falsification suite

The first four files assert that the scorer matches the decisions it was designed
to produce. **That is consistency, not correctness** — grading a scorer against
its own spec measures nothing an outsider should trust.

`tests/test_falsification.py` is written the other way round. Each test states a
property that must hold for *any* correct implementation, derives the expected
answer independently of `src/evidence/score.py`, then asks the scorer. **If the
scorer is wrong, these fail.**

| Property | Statement |
|---|---|
| Monotonicity | adding a corroborating source may never *lower* authority, at any amount |
| Scale invariance | scaling all figures by 1000× changes nothing; the cap is proportional, not absolute |
| Independence | a source asserting nothing cannot corroborate; two documents of the same origin are not independent |
| Boundary | the tolerance edge is asserted from **both sides**, and its asymmetry is explained |
| Refusal safety | an unverifiable source cannot authorise, cannot corroborate a real one, and a stale one carries less authority |

**The meta-test:** `test_these_tests_can_actually_fail` asserts the harness is
live — a deliberately wrong expectation must raise. A falsification suite that
cannot fail is decoration.

**What the falsification suite found while being written:** the tolerance is
**1% of the larger claimed figure**, and the larger figure *moves with the
difference* — so the boundary is a fixed point, not a constant. For a 12,000
instruction the upward edge is **121.21** and the downward edge is **120.00**.
That asymmetry is correct: when a second source claims more, the figure it is
measured against is its own. **The test was wrong, not the scorer** — and
asserting both edges now means a future change to the tolerance model cannot
quietly move them.

---

## Bugs the tests found, with the fix

These are listed because a project about verification that does not report its
own defects is not making an argument, it is making a claim.

### 1. `AUTHORISE_CAPPED` was unreachable

The capped-authority tier was dead code: the corroboration threshold sat above
what a single-source instruction could score, so Case B returned the same
decision as Case D. **Caught by `test_single_source_pays_only_the_cap_and_the_ledger_shows_the_shortfall`**.

### 2. A fabricated source could manufacture authority to pay in full

An uncited source still counted toward corroboration. A model returning a
plausible-looking document with no citation could therefore push an
unauthorised payment into the full-authority tier — the exact failure the
analyst/gate split exists to prevent.

`test_a_hallucinated_claim_cannot_change_the_decision` asserts the decision is
**unchanged** when a claim arrives with an empty `evidence` field, and that
`independent_sources` stays at 1 — so a fabricated second source does not even
count toward corroboration. The fix: a claim with no verbatim citation is
dropped at conversion, before scoring ever sees it.

### 3. `"Subtotal"` matched a `total` pattern

The extractor read **11,000.00** — the subtotal — instead of **12,000.00**.
The component guard now disqualifies sentences naming a subtotal, VAT, tax,
rate, fee or discount outright rather than merely deprioritising them.

### 4. `PO-88214` was read as an amount of 88,214

A purchase order's *number* was extracted as its *total* — a figure the document
does not contain. It broke **two** demo cases at once, because the same file
appears in Case A and Case C, so the case that should have paid in full was
reading an invented number.

Fixed with three explicit guards (currency symbol, decimal/thousands separator,
identifier prefix), replacing the `len(number) <= 4` test that a five-digit
reference defeated.

**The strongest available check now exists as a test:**
`test_no_sample_yields_an_amount_that_does_not_appear_in_the_document` asserts
every extracted figure is findable verbatim in its source. A fabricated amount
cannot satisfy it, by definition.

---

## The video (submitted separately, not in this repository)

The demo recording is submitted through the hackathon form as a link, not
committed here — the video is a build product produced by the pipeline scripts
that lived under `video/` during development (excluded from this repo because
they hardcode the build machine's paths and add ~6 MB of binary payload).

The recording was still verified the same way as everything else:

| Claim | How it was checked |
|---|---|
| All four cases appear | frames grabbed per beat and read from the rendered pixels |
| The ledger shows all four rows | read from the final frame: PAID, PAID (capped), ESCALATED, REFUSED |
| The four totals appear | read from the final frame: 15,000 / 12,000 / 12,000 / 9,000 |
| Caption matches footage | frame at each beat read and compared to the caption text |
| **The narration is present** | zero-crossing rate **0.05–0.09** at all 8 cue windows (speech is 0.05–0.15; a sine drone measures ~0.007) |
| No clipping | `max_volume: -5.2 dB` |

**A verification lesson worth keeping:** an earlier mix measured *louder* than
the bed alone (-19.4 dB vs -27.4 dB), which looked like the voice was present.
The waveform was unchanged — zero-crossing rate 0.0023 in both, where speech is
0.05–0.15. The cause was an ffmpeg filter-graph bug (one output consumed twice;
`asplit=2` fixes it). **Level measurements alone were misleading here** — a
level rise is consistent with speech *and* with a ducked drone. The
zero-crossing test distinguishes them, and it is the test that should have been
run first.

---

## Security and handling

| Check | Result |
|---|---|
| No credentials in tracked files | **verified** — `.env` is git-ignored, and a scan for the real key value across every tracked file returns nothing |
| The sandbox key is scoped | Payments + Beneficiaries only; the key that can't reach other products can't leak them |
| Evidence documents are data, not instructions | the extractor reads them as text; nothing in them is interpreted as a command |
| Unknown fields in a response are ignored | an Airwallex API version change adds optional fields; the client does not assert on unknown keys |

---

## Not verified

Stated plainly rather than omitted.

**1. The Airwallex sandbox client has never executed a successful call.**
`AirwallexRail` is written from the documented shapes. The authentication
request has been corrected against Airwallex's own reference — `x-client-id` and
`x-api-key` as headers against `https://api-demo.airwallex.com/api/v1/` — and
now reaches the API, returning a JSON `credentials_invalid` rather than an edge
HTML 403. **No token has been obtained and no transfer has been created.** Until
that happens every payment in this repository is the mock's.

**2. Provenance is declared, not cryptographically verified.** ATTEST detects
*structural* weakness — uncorroborated claims, contradictions between sources,
unverifiable origins. It does not detect a well-forged document that agrees with
a forged purchase order. An attacker controlling two origins can corroborate a
false instruction.

**3. "Independent origin" is a coarse proxy.** Two sources count as independent
when their declared origins differ. An invoice and a purchase order from the same
compromised mailbox are treated as corroborating.

**4. The thresholds are judgements, not findings.** 25% for the capped tier, 30
days for staleness, 1% for amount tolerance. Visible in one block at the top of
`src/evidence/score.py`. There is no labelled corpus to fit them to.

**5. The extractor is tuned to the sample documents' shapes.** It reads totals,
IBANs and references reliably from invoice-like text. It is not a general
document parser.

**6. No external audit.** Written for a hackathon, tested to the standard above,
not reviewed by anyone else.

**7. The narration in the video is synthesised** (OpenAI `tts-1-hd`, "onyx").
It is not the author's voice. Disclosed here and in the README.

---

## What would falsify the central claim

Stated so a reader can check the argument rather than the demo.

**The claim:** the decision changes with the evidence, and the four tiers are
distinct.

**What would break it:**
- Any two of the four cases producing the same decision from different evidence
  — covered by `test_all_four_outcomes_are_distinct_for_the_same_instruction`
- A source with no citation changing a decision — covered by
  `test_a_hallucinated_claim_cannot_change_the_decision`
- The gate acting on an assessment it cannot read — covered by four tests that
  assert it raises instead
- Adding corroborating evidence lowering authority — covered by
  `test_adding_a_corroborating_source_never_lowers_authority`
- An empty document counting as a corroborating source — covered by
  `test_a_source_with_no_facts_cannot_corroborate`
- Two copies of one document manufacturing authority — covered by
  `test_two_sources_of_the_same_origin_are_not_independent`

**What would NOT break it, but should concern a reader:** the thresholds in
limitation 4 are chosen to be defensible, not fitted. A different reviewer could
set the cap at 10% or 50% and the four-tier structure would survive unchanged.
The *structure* is the contribution; the specific numbers are a policy choice.
