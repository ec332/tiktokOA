# Brief → evidence

Where each requirement of the brief is shown, using two real runs on the user's own feature branch:
`~/Desktop/full-stack-fastapi-template`, `feature/item-pricing` @ `6838b40` (items get a price and a discount; responses show `final_price`), compared with `master`.

- **r-0002**: the reference run. The input was a one-sentence brief, the 4 item routes, and the answers given at Clarify. 27 tests approved; 6 defects (5 causes); the rounding fix was proven by retest. AI cost $0.041.
- **r-0001**: an environment failure, handled as one.

Open either at http://localhost:5173 (Runs → r-0002). Every number in its report links to a record.

---

## 1. AI in understanding and generation; what is deterministic, what needs a human

| Part | Done by | Seen in r-0002 |
|---|---|---|
| Requirements from the brief and the code | **AI** (spec), each one citing code lines that fixed code checks exist | Step 1: 13 requirements (C-1, C-2 from the brief; O-1…O-11 read from the code) |
| What "correct" means | **Human** (Clarify): 5 questions, 9 "the code does this, is it intended?" checks, 3 follow-ups | Step 2: rounding → half up; discount 100 → allowed (typed with Other…); negative price / null discount on update → 422 |
| Test design | **AI** (cases); expected values from **fixed calculators** (`percent_off`: Decimal, half up) | Step 3: TC-O5-2 expects `0.03` for price 0.05 at 50% off; TC-C1-1 expects `16.99` |
| Which tests get built | **Human** (Approve), grouped P0 likely defects / P1 the feature / P2 other checks | Step 3: 27 of 27 signed |
| Test code | **AI** (generate), accepted only by **fixed** static checks and a dry run | Step 4 |
| Admission, verdicts, failure class, localisation, report, retest proof | **Fixed code** | Steps 4–5 |
| Fix suggestion | **AI**, labelled; fixed code checks it applies; proven only by retest | Report → defect cards |

A decision the product owner makes overrides the AI. Every answer reaches the test designer, and a fixed check flags any test that contradicts an answer (e.g. the wrong rounding mode).

## 2. Generation → data and environment → execution → judgement → report; normal and edge cases

r-0002, in order (the run page's right-hand **Activity** panel shows each step live):

1. **Generation:** 27 approved cases → test files (`bound_tests` artifact; hashes locked).
2. **Data and environment:** the branch is cloned and built as its own Docker stack; data comes from fixed factories (`user`, `item`, …). The gate runs each test in a rolled-back transaction; execute runs over HTTP against the real stack. Environment probes (health, DB migration head, seed) run before and after every batch.
3. **Execution:** 36 test points × 3 runs on the real stack → 27 PASS, 9 CONSISTENT_FAIL.
4. **Judgement:** gate (5 stable runs + 1 shuffled; mutants planted in the changed lines) → triage rules.
5. **Report:** defects by cause, problematic tests, areas to improve, rules used.

| Kind | Examples (r-0002) |
|---|---|
| Normal flow | TC-C1-1 discounted price 16.99 · TC-O1-1 create with price and discount · TC-O7-1 price set to null clears `final_price` · TC-O2-1 no price → `final_price` null |
| Boundary | TC-O3-3 discount 99 / **100** / 101 → 200 / 200 / 422 · TC-O3-4 −1 / 0 / 1 · TC-O4-2 price −1 / 0 / 1 · TC-O5-2 half-cent tie |
| Exception | TC-O8-x negative price on update → 422, not 500 · TC-O9-x null discount → 422 · TC-O11-3 non-owner read → 403 |

## 3. A real defect, localisation evidence, retest after the fix

**Defect O-5: half-cent prices round the wrong way.** Not seeded: it is in the user's branch.

| | Evidence |
|---|---|
| Failing request | `POST /api/v1/items/` `{"price": "0.05", "discount_percent": 50}` → 200 |
| Failed assertion | `TC-O5-2/percent_off`: expected `0.03`, actual `0.02` (consistent fail, 3 of 3 runs) |
| Where | Suspects: `backend/app/api/routes/items.py:62` (handler of the request), **`backend/app/models.py:112`** (the `final_price` field the requirement cites) |
| Raw records | `ex:reference/a1/TC-O5-2` (request/response), `log:x18.reference.a1/TC-O5-2/5` (backend log line, joined by trace id) |
| AI fix (labelled) | `round(…, 2)` uses ROUND_HALF_EVEN; quantize with ROUND_HALF_UP. "applies cleanly" |
| **Retest** | **FIXED_CONFIRMED**: branch + patch built as a separate image; **same test files** (`bound_tests` sha256:69aed466…). TC-O5-2: CONSISTENT_FAIL 3/3 before → PASS on **all 5** runs after. 27 passing tests re-checked, **0 regressions**. The other defects still fail after this fix, as they should. |

Other defects in r-0002: O-3 (a 100% discount is rejected), O-8 (negative price on update → 500), O-9 (null discount on update → 500).

## 4. Wrong tests, environment failures, unstable tests, raw records, "a rerun is not proof"

| Concern | How the platform handles it | Shown in |
|---|---|---|
| **Environment failure** | Probes before and after each batch; a stack that won't come up makes every result NOT_RUN → class **ENV**, never a product defect | **r-0001**: the backend's health check failed during bring-up (`up_error`); all 62 results → ENV; report: **0 defects**, not 62 false ones (`data/runs/execute-7/`) |
| **Unstable tests** | Each test runs K = 5 times plus once in shuffled order (seed recorded); disagreement → FLAKY, never a defect | r-0002 gate: stability K5 + shuffle `69aed466`; all stable |
| **Weak tests** | Mutants planted in the branch's changed lines and handlers; a test that catches none is WEAK | r-0002: 15 WEAK, listed (collapsed) under Problematic tests |
| **Wrong test cases** | The cases step drops expected values not traceable to a requirement; a failure that comes from the test code itself is classed as a test defect, not a product defect; answers that contradict a test are flagged at Approve | r-0002: 0 conflicts at Approve. **Known limit:** O-6 is a wrong test (it compared `discount_percent` with the expected `final_price`). It is still reported as a product defect; the AI fix suggestion says the test is wrong and offers no patch. |
| **Raw records** | Every request, response, assertion and backend log line, sealed (sha256 manifest, write-once), linked from the report | `data/runs/execute-18/` (`records.sha256`, `manifest.json`, `results.jsonl`, `logs/`) |
| **A rerun is not proof** | FIXED_CONFIRMED needs the **same test hashes**, fail before, pass on **every** one of K runs after, and no regressions; anything less is NOT_FIXED or INCONCLUSIVE | r-0002 retest above |

## Known limits

- One feature repo (this FastAPI template): the data factories and the in-process harness are written for its API.
- A wrong test can still reach the report as a product defect (O-6 above). The fix suggestion's explanation is the signal.
- Execute runs on one shared database. A defect that stores bad data can make a later test fail too.
- The localiser ranks the request's handler first; for validation and computed-field bugs, the requirement's cited line (ranked second) is the right one.
