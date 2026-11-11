# Stage 11: Deliver (technical reference)

[← Guide](../GUIDE.md) · previous: [Retest](10-retest.md)

| | |
|---|---|
| Module | `tp/stages/deliver/stage.py` (`DeliverStage`) |
| Version | `1.0.0` |
| Consumes | `triage`, `retest`, `bound_tests`, `gate_report`, `cases`, `spec`, `run_options` |
| Produces | `delivery` |
| Model | none: a score and hard rules decide |
| Person | turns publishing on (`TP_PUBLISH=1`) |
| Events | `pr_opened`, `issue_drafted` |

## Purpose

Deliver turns each real product-side problem into something the repository's owners can act on: either a **draft PR** with the tests and a fix that the retest proved, or an **issue draft** with a reproduction. Nothing leaves the machine unless publishing is switched on.

## What it considers

- Real defects classed `PRODUCT_DEFECT` or `INTERMITTENT_PRODUCT`.
- Every `SPEC_MISMATCH` finding on reference: a server/spec disagreement, which can only ever be issue-only.

Seeded defects are never delivered.

## The PR decision (`priority.pr`)

**Score** = severity × evidence, using `config/priority.yaml`.

| Severity (from the failed status assertion) | Weight |
|---|---|
| `server_error`: the actual status is ≥ 500 | 3 |
| `permission_bypass`: a 2xx where 401/403 was expected | 3 |
| `validation_gap`: a 2xx where another 4xx was expected | 1 |
| `wrong_value`: anything else | 2 |

| Evidence | Factor |
|---|---|
| `stable` | 1.0 |
| `plus_planted_bug_or_property`: a test of the defect killed a seed or mutant, or is a property or family check | 1.5 |

A PR is opened only if **score ≥ `threshold` (3)** and **every hard rule holds**:

| Hard rule | Holds when |
|---|---|
| `product_defect` | The class is `PRODUCT_DEFECT` |
| `fixed_confirmed` | Its retest verdict is `FIXED_CONFIRMED` |
| `not_external` | The target isn't a live system |
| `owned_fork` | A fork of the repo that the `gh` account owns exists (`only_forks_we_own`) |
| `under_daily_cap` | Fewer than `max_per_repo_per_day` (3) PRs opened on that fork today |
| `publishing_on` | `TP_PUBLISH=1` |

## A PR (`open_pr`)

1. A branch `tp/fix-<run>-<criterion>` from the tested ref, on the owned fork.
2. One commit with the defect's trusted tests, chosen as for **Export**, plus the harness files.
3. One commit with the `fix_patch` the retest proved.
4. `gh pr create --draft`. The body has:
   - the defect;
   - the criterion;
   - the retest numbers (fail before, pass after, regressions);
   - the test files;
   - the suggestion's explanation;
   - a **disclosure**: the cases and tests were written with an AI model, the fix was proposed by Claude Haiku, and it was accepted only because the failing tests pass after it 5 times out of 5 with the same test hashes.

If any `git` or `gh` call fails, the PR falls back to an issue draft, with the error recorded in `pr_failed`. Every `git`/`gh` call goes through `sh()`, with `GIT_TERMINAL_PROMPT=0` and a 300 s timeout.

## An issue draft (`issue_md`, `repro`)

Written to `data/runs/<run>/issues/<defect>.md`, with a runnable `<defect>.sh`:
- the defect, its criterion, expected vs observed;
- a `curl` reproduction using the target's auth scheme, with the secret always given as `$TOKEN` and the base URL from the environment;
- a Schemathesis command for the operation;
- the PR score with its parts and threshold;
- **why no PR was opened**: each hard rule that failed, a score below the threshold, or the PR error;
- the "drafted by an automated test platform" disclosure.

Issue drafts are never posted automatically.

## Output: `delivery`

```
{defects[{defect, ac, class, score{score, parts, threshold, rules{…}, pr},
          pr?{url, branch, …} | issue?{markdown, repro{curl, …}, file, why[]}, pr_failed?}],
 fork, live}
```

## Files and tests

Tests: `test_deliver.py`, which scripts every `git`/`gh` call through `sh()`.
