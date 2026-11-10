# Evidence: real runs, stage by stage

Every file under `runs/` was copied unchanged from the platform's own store (`data/artifacts/`, `data/runs/`) on 2026-11-10. Nothing was re-run or edited for this export. The one exception is `compare-r-0079-r-0080.md`: `tp compare` recomputed it from these stored records, without re-running either system.

Check that nothing changed since the platform wrote it:

```bash
uv run python evidence/verify.py
# 130 artifacts, 15 sealed run folders (15 against a recorded root)
# all intact
```

- **An artifact** is `<name>.<first 12 hex of its id>.json`, holding `{manifest, body}`. Its id is the sha256 of the canonical manifest and body (`tp/core/artifacts.py`), so one changed byte fails the check. `spec_md` and `signoff_md` are also given as plain `.md` files.
- **A sealed run folder** (`execute-N/`) is write-once, and `records.sha256` seals it. The seal's own hash is recorded in the run's artifacts (`tp/stages/execute/record.py`).
- **Layout:** `runs/<run>/<NN>-<stage>-e<execution id>/`, in the order the stages ran. A stage's working folder (`generate-exec-N/`, `gate-exec-N/`, `report-exec-N/`) sits next to its artifacts. `issues/` holds the issue drafts a run wrote locally; none were published.

## The runs

| Run | System under test | What it shows | Isolation |
|---|---|---|---|
| r-0002 | full-stack-fastapi-template, branch `feature/item-pricing` | The whole flow, with a person at Clarify and Approve. A real defect is found, fixed and proven by retest | 0 |
| r-0001 | the same | An environment failure is reported as ENV, not as 62 defects | 0 |
| r-0076, r-0072 | RealWorld: aspnetcore (C#) and express-bookshelf (JavaScript) | Onboarding from a repo URL, with no target code | — |
| r-0079 → r-0080 | RealWorld, both implementations | The full pipeline on one, then the same sealed tests on the other. A real defect is found by the disagreement | 0 |
| r-0051, r-0053 | Memos (Go) | Discovery: a stand-in setting and a browser flow, each proposed by the model and kept only after running verified it | — |
| r-0055 | Memos | A one-line brief gives cases that ran, plus a browser flow and a webhook stand-in | 1 |
| r-0061 | The notify fixture (`tests/fixtures/sut`, built to host **seeded** defects) | Four seeded defects are caught and retested | 1 |
| r-0046 | A Paperless-ngx the platform didn't start, reached by URL and token | Safe mode on a live system. The report header before and after a design change | 2 |

Isolation: 0 = isolated; 1 = partly isolated (outside services replaced by local stand-ins, labelled simulated); 2 = a live system on this Mac that the platform didn't start.

## The flow on r-0002 (the template branch, attended)

Paths are under `runs/r-0002-template-real-defect/`.

| Stage | Done by | Look at | What the record says |
|---|---|---|---|
| Input | person | `01-input-e12/requirements.*.json` | The brief: "Items get an optional price and a discount percentage; responses show the discounted price." |
| Spec, then Clarify | AI drafts, a person decides | `02-spec-e13/spec_md.md`, `spec.*.json` (`criteria`, `decisions`) | 13 criteria, all confirmed. 8 decisions from Clarify, e.g. `round-half-up`, `allow 100 so final_price is 0` |
| Cases, then sign-off | AI designs; fixed calculators give the expected values; a person signs | `03-cases-e14/signoff_md.md`, `cases.*.json` | "27/27 lines approved", signed `e02ed73bdb46` |
| Generate | AI writes the test code; static checks and a dry run accept it | `04-generate-e15/bound_tests.69aed4668bd1.json`, `generate-exec-15/` | The test files and their hash, which every later stage and the retest refer to |
| Gate | fixed code | `05-gate-e16/gate_report.*.json`, `kill_matrix.*.json`, `gate-exec-16/` | K = 5 runs plus one shuffled run (seed `69aed466`), then planted bugs. ADMITTED 4, ADMITTED_REDUNDANT 8, CANDIDATE_DEFECT 9, WEAK 15 |
| Env | fixed code | `06-env-e17/env_spec.*.json` | The stack and its fingerprint |
| Execute | fixed code | `07-execute-e18/execute-18/` (`results.jsonl`, `logs/`, `probes.json`, `records.sha256`) | Every request, response, assertion and backend log line, sealed |
| Triage | fixed code, plus a labelled AI fix suggestion | `08-triage-e19/triage.*.json` | 6 PRODUCT_DEFECT. O-5's suspects: `backend/app/api/routes/items.py:62`, `backend/app/models.py:112` |
| Report | fixed code | `09-report-e20/report-exec-20/report.html` | "P0 defects: 6" |
| Retest | fixed code | `11-retest-e22/retest.*.json`, `execute-22/` | O-5 **FIXED_CONFIRMED**: TC-O5-2 was CONSISTENT_FAIL 3/3 before and PASS 5/5 after, on the same bound tests. 27 passing tests re-checked, 0 regressions |

**An environment failure, r-0001** (`runs/r-0001-template-env-failure/`): the stack's health check failed at bring-up. `07-triage-e8/triage.*.json` classes all 62 findings ENV, and the report (`08-report-e9/`) shows "P0 defects: 0", not 62 false ones. The sealed records are `06-execute-e7/execute-7/`.

## Beyond the template

| What | Look at | What the record says |
|---|---|---|
| Onboarding from a repo URL | `runs/r-0076-aspnetcore-discover/02-discover-e505/target_profile.*.json`, `discovery_probes.*.json` | Each field has a source, a layer and a probe number. Compose, port, health, login and token scheme were found by algorithm or convention, then probed. The spec was the one question answered by a person (the RealWorld pack's spec). r-0072 is the same for express-bookshelf |
| Model proposals kept only after running verified them | `runs/r-0051-…/02-discover-e356/`, `runs/r-0053-…/02-discover-e369/` (`target_profile.*.json`) | `guard MEMOS_WEBHOOK_PRIVATE_NETWORK_ALLOWLIST` and `ui create_creatememo`, both with source `ai_proposed+verified` |
| Tests transfer unchanged | `compare-r-0079-r-0080.md`; `runs/r-0079-…/04-generate-e531/bound_tests.4c2a0e193733.json` | Both runs used bound tests `sha256:4c2a0e19…`. AGREE 29, TO_PERSON 6, SPEC_MISMATCH 5, INCOMPLETE 4 |
| A real defect, found by the disagreement | `compare-r-0079-r-0080.md` (`G:properties:POST /api/articles`); `runs/r-0080-…/09-report-e547/report-exec-547/report.html`; `07-execute-e545/execute-545/` | aspnetcore answers 201 (the witness), express-bookshelf answers 500 to the same spec-valid body. Report: "P0 defects: 1" |
| Its retest | `runs/r-0080-…/11-retest-e550/`, `12-retest-e551/` | e550, a first patch: still CONSISTENT_FAIL 5/5. e551: PASS 5/5, 31 checked, 0 regressions, `contract_diff: []`. The verdict is **INCONCLUSIVE**, and `unsure` names the login check that the app's sign-in rate limit kept from being observed |
| A rate limit is an environment problem | `runs/r-0080-…/09-report-e547/report-exec-547/summary.md` | "not run: SUSPECT_ENV 2/55": attempts that met a 429 aren't judged |
| A browser flow | `runs/r-0055-…/07-execute-e387/execute-387/results.jsonl` (`U:create_creatememo`) | Passed on 3 of 3 attempts. Proposed by the model in r-0053, and its result is read back over the API |
| A webhook stand-in | `runs/r-0055-…/07-execute-e387/execute-387/dependencies.json` | What the stand-in received. Header: "rests on: real 36/37 (97%), simulated 1/37 (3%)" |
| Seeded defects, labelled, and their retests | `runs/r-0061-notify-fixture-seeded/11-retest-e453/` … `15-retest-e457/` | invite-wrong-recipient, webhook-blocks, webhook-drops-body and page-error each reach **FIXED_CONFIRMED** (3/3 fail before, 5/5 pass after, 25 checked, 0 regressions). e454, the first retest of page-error, was INCONCLUSIVE |
| A live system | `runs/r-0046-paperless-live/` | "isolation 2 (live and local) · Seen on the live system, stable 3/3 · ran 21 of 35" |
| A design iteration: the report's first line | `runs/r-0046-…/09-report-e332/report-exec-332/summary.md` versus `12-report-e348/report-exec-348/summary.md` | Before: "Strongest claim: Seen on the live system, stable 3/3." After: the claim, then its limits, then its basis |

## What the localisation on RealWorld rests on

On a black-box target, the platform localises to the API level: the operation, the request, the failed assertion and the raw answer (here, a 500 carrying SQL text). The file and line (`lib/articles/controller.js:16` → `service.js:7`, and `lib/articles/model.js`) and the patch were worked out by a person with the source, recorded in `docs/status/acceptance/003`. The platform's part is to prove the patch, and it reported that proof as INCONCLUSIVE.
