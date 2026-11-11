# Stage 10: Retest (technical reference)

[← Guide](../GUIDE.md) · previous: [Report](09-report.md) · next: [Deliver](11-deliver.md)

| | |
|---|---|
| Module | `tp/stages/retest/stage.py` (`RetestStage`), `decide.py`, `feed.py` |
| Version | `1.2.0` |
| Consumes | `run_records`, `triage`, `bound_tests`, `gate_report`, `run_options` |
| Produces | `retest` (and a `fix_patch` artifact for each automatic fix) |
| Model | none |
| Person | chooses what to retest |
| Events | `retest_running`, `retest_verdict` |

## Purpose

The retest proves a fix. It reruns **the same tests** against the fixed variant, in a **comparable environment**, and checks three things:
- the defect's tests now pass every time;
- nothing that passed before has broken;
- the API contract under test is unchanged.

A defect closes only on `FIXED_CONFIRMED`.

## Modes

| Mode | How it's started | What it retests |
|---|---|---|
| One defect | `--opt defect=<D-id> --opt after=<variant>`, or **Retest my fix** / **Try suggested fix** in the UI | That defect, on `after` (`fixed:<patch>`, or `reference` for a seeded flag) |
| Automatic | Unattended or focused runs with no `defect` given | Every real `PRODUCT_DEFECT` whose triage suggestion `applies`. Each patch is stored as a `fix_patch` artifact and run as `fixed:<id>`, all in one execute call |
| Nothing | Attended run, no defect given | `{"retests": [], "note": "no fix supplied"}` |

Missing `after`: `retest needs --opt after=<variant> (reference or fixed:<patch>)`. Unknown defect: `no defect '<id>' in triage (known: …)`.

## Algorithm (`_one`)

1. **The failing records are intact.** `record.verify` must pass; otherwise `failing run records were changed`.
2. **Before.** The verdicts on the defect's variant, and the nodes whose case belongs to the defect.
3. **Same tests** (`same_tests`). For each BOUND file, compare (sha256 of the source, `lock_hash`) between the failing run's `bound_tests` (from its manifest) and the current one. Any difference gives `RETEST_INVALID`, and **nothing runs**.
4. **Reproduced** (`reproduced`). Every defect node must have really failed before: not `PASS`, not `SUSPECT_ENV`, not `NOT_RUN`. If not, the result is `INCONCLUSIVE` and nothing runs.
5. **After.** Run execute again with `variants=[after]` and `k = 5` (`decide.K`), which gives a new sealed folder.
6. **Same environment** (`env_diff`). Compare the before and after fingerprints field by field, ignoring the app's own image and container, the flags and the code. A difference in `sut` name or ref is listed. Patches may only be **added**; a patch that was changed or removed is listed. A missing fingerprint is listed too.
7. **Same contract** (`contract_diff`). For every operation a failing attempt was under test on, resolve its full operation object (all `$ref`s followed) in the before and after served specs and compare. Any change is listed. A call that matches no operation in the before spec is listed too, so the check fails closed. If the spec isn't served, the contract counts as changed.
8. **Judge** (`decide.judge`).

## Verdict rules

| Order | Condition | Verdict |
|---|---|---|
| 1 | Tests changed | `RETEST_INVALID` |
| 2 | Not reproduced before, **or** the environment differs, **or** any defect test or previously passing test was `SUSPECT_ENV`/`NOT_RUN` after (`unsure`), **or** the contract changed | `INCONCLUSIVE` |
| 3 | Any defect test isn't `PASS` after | `NOT_FIXED` |
| 4 | Any test that passed before doesn't pass after | `FIXED_WITH_REGRESSION` |
| 5 | Otherwise | **`FIXED_CONFIRMED`** (`closed: true`) |

`fix_kind` records what made the difference:
- `own patch on upstream` for `fixed:…`;
- `flag off (seeded)` for `bug:…` → `reference`.

Retesting a seeded defect against `reference` is how seeded defects reach `FIXED_CONFIRMED` (r-0061).

**K is fixed at 5.** A defect that fails at rate *p* would need *(1−p)^K < 0.05* for a clean pass to prove the fix. That holds for every defect seen so far, which fail every time.

## Output: one entry of `retest.retests[]`

```
{defect, ac, class, fix_kind, fix_patch?, bound_tests, tests_changed[],
 before{variant, run_records}, after{variant, folder, root, k, summary},
 env_diff[], reproduced, verdict, closed,
 fail_to_pass[{node, case_id, before, before_dist, after, after_dist}],
 pass_to_pass{checked, regressions[]}, unsure[], pre_existing[], contract_diff[]}
```

On r-0002, O-5: `fail_to_pass` CONSISTENT_FAIL 3/3 → PASS 0/5; `pass_to_pass.checked` 27; regressions `[]`; `FIXED_CONFIRMED`.

## Iteration feed

`tp retest feed --gate-report <id> --report <id>` turns the report's gaps into an `iteration` artifact for a second cycle against the same frozen spec. The candidates are, in order, at most `CAP = 10` criteria:
1. criteria with no admitted test;
2. criteria with test-gap survivors, most first;
3. criteria with deferred cases.

Feed it back with `tp stage run cases --input spec=<id> --opt iteration=<id>`. Unbound cases aren't candidates, because new cases can't close a binding gap.

## Files and tests

Tests: `test_retest.py`, `test_feed.py`.
