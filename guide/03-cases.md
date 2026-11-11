# Stage 3: Cases, then Sign-off (technical reference)

[← Guide](../GUIDE.md) · previous: [Spec + Clarify](02-spec.md) · next: [Generate](04-generate.md)

| | |
|---|---|
| Module | `tp/stages/cases/stage.py` (`CasesStage`), `build.py`, `calculators/`, `templates/*.yaml` |
| Version | `1.2.0+p<prompt hash>` |
| Consumes | `spec`, `run_options` |
| Produces | `cases`, `signoff_md` |
| Model | drafts case designs and revises rejected lines; **never** supplies an expected value from a calculator |
| Person | `signoff` (line by line; at most 3 rounds) |
| Events | `cases_drafted`, `cases_acceptance_redraft`, `cases_signed` / `cases_unsigned` |

## Purpose

This stage chooses the concrete situations to test for each in-scope criterion. Each one gets an expected value computed by fixed code, or quoted from the criterion. A person then signs off on exactly what will run.

## Algorithm

### Scope

| Criterion | In scope? |
|---|---|
| `out_of_scope` | No: "out of scope in the spec" |
| Its flow isn't in `flows` (white-box default `FLOW-items-crud`) | No: "flow … not in this run" |
| `routes` or a scan was given | Every flow is in scope |
| An `iteration` was given | Only that iteration's candidate criteria, each with its gaps |

### Drafting (`_draft`)

1. **Minimum techniques.** `signals.minimum_templates(c)` lists the techniques a criterion must get, based on its wording: a limit implies `boundary`, `must`/`required` implies an error path, and so on. `templates` can restrict the set.
2. **Rules.** `build.decision_rules(spec.decisions)` turns the Clarify answers into rules, plus any New run `rules.cases`. Answers override criterion text that disagrees with them.
3. **Draft.** `ai.draft` returns, for each criterion: `cases`, `boundaries` (field, limit, inclusive) and `tables` (decision tables), plus any minimum templates it skipped, each with a reason.
4. **Build units.** `build.units_from_draft` builds each unit:
   - **Calculator cases**: the model names a calculator and its parameters, and fixed code runs it. The available calculators are `percent_off` (rounding `half_up`) and `page_window`. A **differential check** compares the result with an independent property (`percent_off_bounded`, `page_len_ok`); any disagreement goes to the attention list.
   - **Literal cases**: the expected value is taken from the criterion's own text (`then` / `status_code`), using `literal()`.
   - **Boundary units**: `boundary_points(limit)` gives the points around the limit, and `boundary_expect` gives each point's outcome. `boundary_monotonic` checks the outcomes switch once, at the limit.
   - **Decision tables**: one row per combination of conditions.
5. **Acceptance fill.** Each criterion needs one `acceptance` case for its normal outcome. If the model skipped any, one more call asks only for those (`cases_acceptance_redraft`).
6. **Preconditions.** These must be factory names from the target's data strategy. Unknown ones become leading `Given: …` steps.
7. **Dedupe and budget.** `dedupe_and_budget` drops duplicates. It keeps the minimum templates and at most **3 model extras per criterion** (`EXTRAS_PER_CRITERION`); the rest are `deferred` with a reason.
8. **Rank.** `build.rank` scores each unit with `config/priority.yaml` `case.weights` and labels it `P0` (≥ 8), `P1` (≥ 5) or `P2`:

   | Weight | Value |
   |---|---|
   | `base` | 1 |
   | `suspected_defect` | 7 |
   | `in_feature` (touches the branch's changed lines) | 4 |
   | `decision_table` | 3 |
   | `rule_interaction` | 2 |
   | `acceptance` | 2 |

9. **Exit problems.** `exit_problems` reports a criterion with no case, a missing minimum template, or a missing draft. `decision_conflicts` reports a case that contradicts a Clarify answer. Each assumed criterion is flagged.

Technique order: `error_guessing`, `decision_table`, `boundary`, `error_exception`, `acceptance`.

### Sign-off (`_ask` → resume)

`build.line(u)` renders each unit as one line with a content hash. Lines approved in an earlier sign-off with the same hash, in the same scope (same SUT ref), are **carried forward** without being asked again.

One `signoff` item holds:
- the lines;
- the attention list, worked boundary examples and not-tested list (round 1 only);
- `also_runs`, the generic checks that will also run, for information only (not signed);
- on a live target, `planned_writes`: every write in the spec with the safe-mode proxy's verdict (`create` / `own_items` / `blocked`).

| Line decision | Effect |
|---|---|
| `approve` | Its hash joins `approved` |
| `reject` (comment required) | `ai.revise` rewrites the unit using the comment, then it is asked again in the next round |
| `exclude` | The unit is dropped and listed as "excluded at sign-off" |
| whole item `defer` | Finish **unsigned** |

After `MAX_ROUNDS = 3`, or on `defer`, the cases are finished unsigned.

### Finishing

The cases are `signed` only if every line's hash is approved (an empty set isn't signed). `signoff.hash` is the sha256 of the sorted approved hashes. Unsigned outputs carry the `unsigned` label. `generate` refuses unsigned cases unless the run is unattended.

## Output: `cases`

```
{spec, sut, flows[], minimum{ac: [technique]}, deferred[], attention[], not_tested[{criterion, reason}],
 cases[{id, ac, flow, technique, purpose, priority, quality?, preconditions[], steps[], data[],
        expected{value, oracle: "calculator:<name>"|"literal", params?}  |  points[...]  |  rows[...],
        defect_hypothesis}],
 signoff{signed, hash, lines, approved, carried_forward[]}}
```

`signoff_md` is the sign-off sheet as markdown.

## Files and tests

- `build.py`: units, dedupe, rank, exit and lines.
- `calculators/__init__.py`: `@calculator` and `@prop` registries.
- `signals.py`: flows and minimum templates.
- `ai.py`: the template catalogue, draft and revise.
- `templates/`: one file per technique.
- Tests: `test_cases.py`, `test_calculators.py`, `test_rank_regression.py`.
