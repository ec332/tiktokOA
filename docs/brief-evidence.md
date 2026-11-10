# Brief → evidence

Where each requirement of the brief is shown. Two sets of real runs:

**The feature branch (iterations 1–2).** The user's own feature branch:
`~/Desktop/full-stack-fastapi-template`, `feature/item-pricing` @ `6838b40` (items get a price and a discount; responses show `final_price`), compared with `master`.

- **r-0002**: the reference run. The input was a one-sentence brief, the 4 item routes, and the answers given at Clarify. 27 tests approved; 6 defects (5 causes); the rounding fix was proven by retest. AI cost $0.041.
- **r-0001**: an environment failure, handled as one.

**The new targets (iteration 3).** Systems the platform had never seen, onboarded from a repo URL with no target code:

- **RealWorld (Conduit)**, one spec and two implementations: aspnetcore (C#) and express-bookshelf (JavaScript). r-0079 runs the full pipeline on aspnetcore; r-0080 runs the same sealed tests on express-bookshelf; `tp compare` judges the two.
- **Memos** (Go, a web UI and a webhook): r-0055, with a browser flow and a webhook stand-in.
- **The notify fixture** (`tests/fixtures/sut`, a small app built for this, **seeded** defects): r-0061.

Open any of them at http://localhost:5173 (Runs → r-NNNN). Every number in a report links to a record.

---

## 1. AI in understanding and generation; what is deterministic, what needs a human

| Part | Done by | Seen in r-0002 | New targets (iteration 3) |
|---|---|---|---|
| Requirements from the brief and the code | **AI** (spec), each one citing code lines that fixed code checks exist | Step 1: 13 requirements (C-1, C-2 from the brief; O-1…O-11 read from the code) | r-0079: from the RealWorld pack's brief and spec. s2: on Memos, a one-line brief gave cases that ran (r-0055, "ran 37 of 42") |
| What "correct" means | **Human** (Clarify): 5 questions, 9 "the code does this, is it intended?" checks, 3 follow-ups | Step 2: rounding → half up; discount 100 → allowed (typed with Other…); negative price / null discount on update → 422 | Unattended runs: criteria stay "assumed (never confirmed by a person)" in the report. A pack can carry settled answers (`packs/realworld/pack.yaml`) |
| Test design | **AI** (cases); expected values from **fixed calculators** (`percent_off`: Decimal, half up) | Step 3: TC-O5-2 expects `0.03` for price 0.05 at 50% off; TC-C1-1 expects `16.99` | r-0079: 55 cases, plus generic families from the spec (`G:properties:…`) |
| Which tests get built | **Human** (Approve), grouped P0 likely defects / P1 the feature / P2 other checks | Step 3: 27 of 27 signed | Sign-off lists everything that will run: case tests, generic families, UI flows, dependency checks and planned writes (DR-11) |
| Test code | **AI** (generate), accepted only by **fixed** static checks and a dry run | Step 4 | Same, on a black-box runner that reaches the target only over HTTP |
| Admission, verdicts, failure class, localisation, report, retest proof | **Fixed code** | Steps 4–5 | Same code on every target |
| Fix suggestion | **AI**, labelled; fixed code checks it applies; proven only by retest | Report → defect cards | r-0061 retests (seeded fixes) |
| **New:** how to reach, start and log in to a target (discovery) | **AI proposes, fixed code verifies by probing**; a person answers what neither can (Clarify) | — | Profiles with a source and layer per field: aspnetcore r-0076, express-bookshelf r-0072 (its database settings answered by a person) |
| **New:** a browser flow (`.ui.yaml`) | **AI proposes** the page actions from a page snapshot; **fixed code** runs it, reads the result back over the API, and gates it K/K. No model acts during a test run (DR-1) | — | Memos r-0053 (proposed, repaired once, verified), r-0055 (`U:create_creatememo` stable at K5, passed) |
| **New:** stand-ins for outside services (webhook, email) | **Fixed code** finds the outbound calls; **AI proposes** a setting it can't read from code; **running verifies** it | — | Memos r-0051: Haiku proposed the private-network allowlist setting, verified by running (provenance `ai_proposed+verified`) |
| **New:** transfer between implementations, and judging disagreements | **Fixed code**: sealed test hashes (DR-5) and the witness rule (DR-6). **Human**: a person checks a disagreement before it is called a defect | — | `tp compare` r-0079 × r-0080: AGREE 29, TO_PERSON 6, SPEC_MISMATCH 5, INCOMPLETE 4 |
| **New:** how isolated a result is, and what it rests on | **Fixed code**: an isolation level (0, 1 or 2) and a basis (real or simulated) on every result (DR-3, DR-7) | — | r-0055 header: "isolation 1 (partly isolated (stand-ins))"; r-0046 rebuilt: "isolation 2 (live and local)" |

A decision the product owner makes overrides the AI. Every answer reaches the test designer, and a fixed check flags any test that contradicts an answer (e.g. the wrong rounding mode).

## 2. Generation → data and environment → execution → judgement → report; normal and edge cases

r-0002, in order (the run page's right-hand **Activity** panel shows each step live):

1. **Generation:** 27 approved cases → test files (`bound_tests` artifact; hashes locked).
2. **Data and environment:** the branch is cloned and built as its own Docker stack; data comes from fixed factories (`user`, `item`, …). The gate runs each test in a rolled-back transaction; execute runs over HTTP against the real stack. Environment probes (health, DB migration head, seed) run before and after every batch.
3. **Execution:** 36 test points × 3 runs on the real stack → 27 PASS, 9 CONSISTENT_FAIL.
4. **Judgement:** gate (5 stable runs + 1 shuffled; mutants planted in the changed lines) → triage rules.
5. **Report:** defects by cause, problematic tests, areas to improve, rules used.

On the new targets the chain is the same, with **discovery** in front: the repo is cloned, its compose file or Dockerfile is rewritten onto an internal network, and the profile says how to start, reach and log in to it. Data comes from the spec (sign-ups and creates the tests make themselves), not from factories.

| Kind | Examples (r-0002) | New targets (iteration 3) |
|---|---|---|
| Normal flow | TC-C1-1 discounted price 16.99 · TC-O1-1 create with price and discount · TC-O7-1 price set to null clears `final_price` · TC-O2-1 no price → `final_price` null | RealWorld TC-C9-2 favourite an article (r-0079) · Memos `U:create_creatememo` in the browser, read back over the API (r-0055) |
| Boundary | TC-O3-3 discount 99 / **100** / 101 → 200 / 200 / 422 · TC-O3-4 −1 / 0 / 1 · TC-O4-2 price −1 / 0 / 1 · TC-O5-2 half-cent tie | RealWorld TC-C3-6, a paging edge (aspnetcore 422, express-bookshelf 200) · generated bodies from the spec's schema (`G:properties:POST /api/articles`) |
| Exception | TC-O8-x negative price on update → 422, not 500 · TC-O9-x null discount → 422 · TC-O11-3 non-owner read → 403 | RealWorld `G:properties:GET /api/articles/{slug}`, an unknown slug → 404 in the spec's error shape · Memos `G:dependency_failure_handled`: the webhook receiver fails and the app still answers (r-0055) |

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

**New targets (iteration 3):**

| | Evidence |
|---|---|
| **Seeded** defects in the new checks (labelled: the notify fixture exists to host them, DR-8) | r-0061: each seed caught by exactly its known-answer check, then retested to **FIXED_CONFIRMED** with the same hashes, no regressions in 25 checks and an unchanged contract: invite-wrong-recipient #453, webhook-blocks #455, webhook-drops-body #456, page-error #457 |
| A **real** defect on RealWorld, confirmed by a person | express-bookshelf answers 500, with its SQL text, to spec-valid `POST /api/articles` bodies, where aspnetcore answers 201 (the witness, r-0079 × r-0080). Localised to two causes: every client key reaches the insert as a column (`lib/articles/controller.js:16` → `service.js:7`), and text Postgres can't store (a NUL character, more than 255 characters) reaches it unchecked (`lib/articles/model.js`). Fixed by a field allow-list and a `storable` validator answering 422 (patch `sha256:b2100c7b…`) |
| Its retest | Same sealed tests: CONSISTENT_FAIL 3/3 before → **PASS 5/5** after, **0 regressions** in 31 checked, contract unchanged. Verdict **INCONCLUSIVE**, not FIXED_CONFIRMED: the app's hard-coded sign-in rate limit kept one passing check (`POST /api/users/login`) from being observed on attempts 2–5, so a regression there can't be ruled out (DR-10). A first patch that fixed only the first cause was caught still failing (exec #550). `docs/status/acceptance/003` |
| Disagreements on record, not confirmed | express-bookshelf answers 500 with its SQL text to an out-of-range `limit` or a negative `offset` (r-0074, integration/004 §1); not fixed (the user dropped it, integration/005) |

## 4. Wrong tests, environment failures, unstable tests, raw records, "a rerun is not proof"

| Concern | How the platform handles it | Shown in | New targets (iteration 3) |
|---|---|---|---|
| **Environment failure** | Probes before and after each batch; a stack that won't come up makes every result NOT_RUN → class **ENV**, never a product defect | **r-0001**: the backend's health check failed during bring-up (`up_error`); all 62 results → ENV; report: **0 defects**, not 62 false ones (`data/runs/execute-7/`) | A target's **rate limit** is an environment problem, not behaviour: express-bookshelf allows 20 sign-ins per 15 minutes; a failed attempt that met a 429 is SUSPECT_ENV and left out of the comparison. r-0080: 6 answers of 429, 2 cases (`data/runs/execute-545/`) |
| **Unstable tests** | Each test runs K = 5 times plus once in shuffled order (seed recorded); disagreement → FLAKY, never a defect | r-0002 gate: stability K5 + shuffle `69aed466`; all stable | r-0079: TC-C2-1, TC-C3-1, TC-C4-1 pass once, then fail (data left by earlier attempts). INTERMITTENT, escalated to 10 more runs, never a defect |
| **Weak tests** | Mutants planted in the branch's changed lines and handlers; a test that catches none is WEAK | r-0002: 15 WEAK, listed (collapsed) under Problematic tests | A new check family counts only after it catches a seeded defect in the fixture (DR-8, r-0061) |
| **Wrong test cases** | The cases step drops expected values not traceable to a requirement; a failure that comes from the test code itself is classed as a test defect, not a product defect; answers that contradict a test are flagged at Approve | r-0002: 0 conflicts at Approve. **Known limit:** O-6 is a wrong test (it compared `discount_percent` with the expected `final_price`). It is still reported as a product defect; the AI fix suggestion says the test is wrong and offers no patch. | **The witness rule (DR-6):** a case that fails on both implementations goes to a person as a suspect test or spec, never as a defect. r-0079 × r-0080: 6 TO_PERSON rows |
| **Raw records** | Every request, response, assertion and backend log line, sealed (sha256 manifest, write-once), linked from the report | `data/runs/execute-18/` (`records.sha256`, `manifest.json`, `results.jsonl`, `logs/`) | `data/runs/execute-534/` (r-0079), `execute-545/` (r-0080); what each stand-in received is in `dependencies.json` |
| **A rerun is not proof** | FIXED_CONFIRMED needs the **same test hashes**, fail before, pass on **every** one of K runs after, and no regressions; anything less is NOT_FIXED or INCONCLUSIVE | r-0002 retest above | Also an **unchanged API contract** for the operation under test (DR-10): a "fix" that changes the contract is refused (r-0061 retests passed it) |

## Known limits

- The data factories and the in-process harness are written for the template's API. Black-box targets get their data from the spec instead, so cases that need a second user or other prepared data stay unbound (9 of 55 on RealWorld).
- A wrong test can still reach the report as a product defect on a single target (O-6 above). The fix suggestion's explanation is the signal. With two implementations, the witness rule catches it.
- Execute runs on one shared database. A defect that stores bad data can make a later test fail too, and a black-box target keeps data between attempts (the INTERMITTENT rows above).
- The localiser ranks the request's handler first; for validation and computed-field bugs, the requirement's cited line (ranked second) is the right one. On black-box targets, localisation is at the API level, plus the handler file where the source is there.
