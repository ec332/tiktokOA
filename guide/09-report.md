# Stage 9: Report (technical reference)

[← Guide](../GUIDE.md) · previous: [Triage](08-triage.md) · next: [Retest](10-retest.md)

| | |
|---|---|
| Module | `tp/stages/report/stage.py` (`ReportStage`), `build.py`, `product.py`, `render.py`, `export.py` |
| Version | `1.1.0` |
| Consumes | `spec`, `cases`, `gate_report`, `bound_tests`, `run_records`, `triage`, `run_options` |
| Produces | `report` |
| Model | none. The only model text shown is the triage fix suggestion, labelled as a suggestion |
| Person | none |
| Events | `report_written` |

## Purpose

The report states what the run can claim, then what limits that claim, then what it rests on. It is built from stored artifacts only, so it can always be rebuilt from them.

## Algorithm

1. **Labels.** The labels of every input artifact are carried through (e.g. `real`, `unsigned`).
2. **Decisions.** `lineage_decisions` walks the parents of every input artifact, finds every stage execution that produced one, and lists all its resolved review items: kind, decision, who decided, and why.
3. **Run facts.** `build.run_facts` reads what the artifacts don't hold:
   - from the target profile: slug, reach, isolation level and dependencies;
   - from the sealed run folder: `results.jsonl` and `dependencies.json`.
4. **Build.** `build.build(...)` produces the sections below. **Every number** is written as `num(n, d, ids)`: a count, a denominator and the ids it counts. If any number lacks that breakdown, the stage fails: `numbers without a composition: …`.
5. **Product view.** `product.build` adds the summary, defects with location and fix, problematic tests, areas to improve and rules used. Every line names the record it came from.
6. **Render.** `report.json`, a single self-contained `report.html`, `summary.md` and a CLI worklist, all in `data/runs/report-exec-<id>/`.

## The header and claim

| Field | Content |
|---|---|
| `claim` | Target, reach, isolation, ceiling, what ran and what was blocked, and the basis |
| `ceiling` | The strongest claim the strategies that ran allow (see below) |
| `labels`, `network`, `strategies`, `variants`, `k`, `cases_signed`, `unattended_decisions` | What the claim rests on |

Example (flatnotes, r-0084):

```
flatnotes · container · isolation 0 (isolated) · Stable, property-checked · ran 27 of 31;
blocked ▸ signed case not bound to a test 4/31 (13%) · rests on: real 27/27 (100%)
```

Claim ceiling (`build.ceiling`):

| Strategies | Ceiling |
|---|---|
| `reach: external` | "Seen on the live system, stable K/K" |
| `mutation: in_process` (white-box) | "Likely defect, catches planted bugs" |
| A `properties` family row that ran | "Stable, property-checked" |
| Otherwise | "Stable" |

**Basis.** Each result counts as `real` or `simulated` depending on whether its attempts rested on a stand-in.

## The worklist

| Priority | Contents |
|---|---|
| **P0** | Defects classed `PRODUCT_DEFECT` or `INTERMITTENT_PRODUCT`, real ones first. Each shows: expected vs observed, cases, root cause, suspects, evidence, decision and basis |
| **P1** | Weak tests (caught no mutant); signed cases not bound to a test, with the recorded reason; criteria in scope with no admitted test |
| **P2** | Test-health findings (`FLAKY_TEST`, `INSUFFICIENT_EVIDENCE`, `TEST_DEFECT`, `ENV`, `DATA`), quarantined tests, redundant tests, mutation strength (`not measured` on black-box) |
| **P3** | Spec gaps; criteria still assumed (never confirmed by a person); server/spec disagreements |

## Other sections

| Section | Contents |
|---|---|
| `checks` | The generic families: each one's claim, its counts, exclusions with reasons, and `SPEC_MISMATCH` groups (the first 10 listed, the rest counted) |
| `dependencies` | Each stand-in: what it was sent and what it received |
| `scorecard` | Criteria, cases, gate statuses and verdicts, as numbers with their composition |
| `traceability` | Requirement → criterion → case → test → verdict → evidence |
| `coverage` | Coverage by technique (boundary, exception, …) |
| `uncovered` | What wasn't tested and why |
| `ai_decisions` | Every place a model proposed something, and what fixed code or a person did with it |
| `evidence` | Excerpts for every cited id |
| `defects` | The triage defects |
| `product` | `summary`, `defects`, `problematic_tests`, `causes`, `improve`, `rules` |

## Export (an action, not a stage step)

`export.export` writes the run's **trusted** tests onto a **new** branch of your repo:
- gate status `ADMITTED`, `ADMITTED_REDUNDANT` or `CANDIDATE_DEFECT`, and not overruled by triage;
- weak, flaky, spec-gap and test-defect tests are listed with the reason they were left out.

The branch starts at the commit the run tested. It gets one commit that adds `generated/` under the repo's tests folder, with a `conftest.py` and `_support/` (the `platform_rt` wheel). The commit is made through a temporary git worktree, so your checkout is never touched, and an existing branch is never overwritten. In the UI this is **Export** on the run page.

## Output: `report`

```
{report: {header, worklist{P0, P1, P2, P3}, checks, dependencies, scorecard, traceability, coverage,
          uncovered, ai_decisions, evidence, defects, product},
 html, summary_md, cli, path}
```

## Files and tests

Tests: `test_report.py`, `test_export.py`. `expected/` holds the expected renders.
