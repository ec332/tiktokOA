# Stage 8: Triage (technical reference)

[← Guide](../GUIDE.md) · previous: [Execute](07-execute.md) · next: [Report](09-report.md)

| | |
|---|---|
| Module | `tp/stages/triage/stage.py` (`TriageStage`), `rules.py`, `localise.py`, `evidence.py`, `propose.py` |
| Version | `1.2.0+p<prompt hash>` |
| Consumes | `run_records`, `gate_report`, `cases`, `spec` |
| Produces | `triage` |
| Model | `claude-haiku-5-5` suggests a fix for located real defects only. It is advisory |
| Person | `triage` (`accept` / `override` with a class and a reason) |
| Events | `triage_classified`, `triage_fix_suggested` |

## Purpose

Triage gives every non-passing verdict exactly **one class**, with its rule and its evidence. It then groups product findings into **defects**, locates each one, and sends it for review.

## Algorithm

1. **Read the sealed records** (`_records`). The sha256 of `records.sha256` must equal the `run_records.root`, and every file must match its line in the seal. Otherwise the stage raises (`… was changed after sealing`).
2. **Classify** each non-passing verdict (`rules.classify(vd, ref, rows)`), using the same test's verdict on `reference` for comparison.
3. **Group** product findings into defects by **(variant, criterion)**. The id is `D-<variant>/<ac>`. The `kind` is `real` on reference and `seeded` on a `bug:` variant.
4. **Evidence and localisation** for each defect (`analyse`):
   - `evidence`: the failing record id, the record id plus its exchange step, and the backend log line;
   - `observed`: method, path, query, body and status;
   - `failed_assertions`;
   - `root_cause`: the traceback's root cause;
   - `suspects`: from the localiser.

   Every cited id must resolve, which the code asserts.
5. **Fix suggestion** (white-box, `real` defects with readable suspect files only; see below).
6. **Review.** One `triage` item per real defect, offering every class as a choice.
7. **Resume.** On `override`, the defect and its findings take the new class, keeping `original_class`, and the override is listed with its reason and who made it.
8. **Quarantine.** Each `FLAKY_TEST` finding, except on seeded variants, gets a quarantine entry that expires after 14 days or 5 runs.

## Classification rules (`rules.classify`)

The rules are tried in order, and the first match wins:

| # | Condition | Class |
|---|---|---|
| 1 | `SUSPECT_ENV`, or `NOT_RUN` with an `ENV…` reason | `ENV` |
| 2 | Other `NOT_RUN` | `TEST_DEFECT` |
| 3 | A failing attempt's origin is `ENV` (but an `ENV` timeout under an injected fault counts as `PRODUCT_ERROR`) | `ENV` |
| 4 | A failing attempt's origin is `DATA` (setup or factory) | `DATA` |
| 5 | No injected fault, and the status under test is only ever 429, 502, 503 or 504 | `ENV` |
| 6 | `INTERMITTENT` with test-side origins only (`BINDING`, `CONFORMANCE`, `PLATFORM`) | `FLAKY_TEST` |
| 7 | `INTERMITTENT` with product-side origins, and escalation confirms it | `INTERMITTENT_PRODUCT` |
| 8 | `INTERMITTENT` otherwise | `INSUFFICIENT_EVIDENCE` |
| 9 | `TIMEOUT` | `INSUFFICIENT_EVIDENCE` |
| 10 | A seeded variant whose reference verdict isn't `PASS` | `SAME_AS_REFERENCE` (not a finding of the variant) |
| 11 | Test-side origin | `TEST_DEFECT` |
| 12 | Origin `SPEC` | `SPEC_MISMATCH` (server or spec is wrong; never a defect) |
| 13 | Gate status `SPEC_GAP` (criterion assumed) | `SPEC_GAP` |
| 14 | `CONSISTENT_FAIL`, product-side origins only (`PRODUCT_ERROR`, `ASSERTION`, `CONTRACT`, `UI_ERROR`) | **`PRODUCT_DEFECT`** |
| — | Anything else | `INSUFFICIENT_EVIDENCE` |

Two contract rules also apply:
- **Malformed path.** If a test sent a path value that the spec's parameter schema refuses (type, format, pattern or enum), and the server refused it, the test is at fault, not the server.
- **Assumed criteria and statuses.** If an assumed criterion failed on a status that the served spec and server agree on, it's a `SPEC_GAP`. If it failed on a status the spec doesn't declare, it's a `SPEC_MISMATCH`.

## Localisation (`localise.suspects`, white-box)

A fixed chain, with at most `MAX_SUSPECTS = 5` suspects, each with its basis:

1. the failing assertion → its HTTP exchange (`under_test`, or the last test request);
2. → the backend log line carrying the trace id → the traceback after it;
3. → the innermost **app** frame (file:line, mapped between the pinned upstream file and the patched branch with `line_map`);
4. → the request parameter in the handler's signature;
5. → the clause a DB error names (`InvalidRowCountInLimitClause` → `.limit(`, `…OffsetClause` → `.offset(`, `NotNullViolation`);
6. → the criterion's code refs.

On seeded defects, the localiser is scored against the flag's real sites, giving `rank` and `top1`/`top3`/`top5` in `scores`. That is how localisation quality is measured.

On **black-box** targets there is no source, so `suspects` is empty. A defect is located at the API: the operation, the request, the failed assertion, the raw answer and the backend log.

## Fix suggestion (`propose.suggest`)

- **Input:** the defect, its criterion, and the branch's text of up to 5 suspect files.
- **Model output** (schema-enforced): `{explanation, edits[{file, find, replace}]}`.
- **Fixed checks:** each `find` must occur **exactly once** in its file, and the edited file must still parse. Fixed code then builds the unified diff itself.
- **Result:** `suggestion{explanation, edits, patch, applies, problems}`. It is labelled as a suggestion in the report. It counts only if the retest proves it: "Try suggested fix", or the automatic retest in unattended runs.

## Output: `triage`

```
{findings[{id, node, case_id, ac, variant, verdict, dist, class, rule, evidence[], seeded, also_fails_on, original_class?}],
 defects[{id: "D-<variant>/<ac>", variant, ac, class, kind: real|seeded, flag, findings[], cases[],
          evidence[], observed, failed_assertions[], root_cause, suspects[{file, line, upstream_line, text, basis}],
          suggestion?, review, decision{by, decision, reason}, original_class?}],
 scores[], evidence{id: excerpt}, overrides[], quarantine[], run_records, summary{CLASS: n}}
```

## Files and tests

Tests: `test_triage.py`, `test_contract_rules.py`.
