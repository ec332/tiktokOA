# Stage 5: Gate (technical reference)

[← Guide](../GUIDE.md) · previous: [Generate](04-generate.md) · next: [Env](06-env.md)

| | |
|---|---|
| Module | `tp/stages/gate/stage.py` (`GateStage`), `decide.py`, `mutants.py`, `handlers.py`, `blind.py`, `ai.py` |
| Version | `1.1.0+p<prompt hash>` |
| Consumes | `bound_tests`, `cases`, `spec`, `run_options` |
| Produces | `gate_report`, `kill_matrix` |
| Model | proposes which surviving mutants are *equivalent* (advisory; a person decides) |
| Person | `equivalent_mutant` (`equivalent` / `not_equivalent`) |
| Events | `gate_stability_run`, `gate_stability`, `gate_mutants_generating`, `gate_linking`, `gate_matrix_start`, `gate_matrix`, `gate_cache`, `gate_done` |

## Purpose

Before any result counts, the gate proves that each test is **stable** and **can fail for the reason it claims**. The decisions are pure functions over result records (`decide.py`), so every rule has a fast unit test.

## Settings

| Setting | Default | Limits | Meaning |
|---|---|---|---|
| `k` | 5 (3 on a live target) | 2–10 | Stability runs on the reference variant |
| `min_kills` | 1 | 1–5 | Kills needed to avoid `WEAK` |
| `near` | 3 lines | 0–20 | A survivor this close to a criterion's code ref is a test gap |
| `every_criterion` | true | — | List in-scope criteria that have no admitted test |

Pass them as `--opt gate=…` or set them on the New run screen; out-of-range values are clamped. `admit=v1` is the legacy mode, which admits any test that passes once.

## Algorithm

### 1. Stability (`_stability`)

All BOUND tests run **k** times on `reference`, then once more with `TP_SHUFFLE=<first 8 hex of bound_tests>`. A node is **stable** if:
- every run gave the same outcome;
- it appeared in every run;
- it had at most one failure origin.

Nodes that needed `eventually` retries but passed every run are recorded as `eventually_consistent`.

### 2. Routing (`decide.route`)

| Condition | Status |
|---|---|
| Not stable | `FLAKY` |
| Stable pass | → admission (step 5) |
| Skipped (excluded by the profile / no valid body) | `EXCLUDED` |
| Origin `SPEC` (server disagrees with its own spec) | `SPEC_MISMATCH` |
| Origin in `BINDING`, `CONFORMANCE`, `DATA` | `REJECTED_ORACLE` |
| Origin not product-side (`ENV`, `PLATFORM`, `TIMEOUT`) | `ERROR` |
| Product origin (`ASSERTION`, `CONTRACT`, `PRODUCT_ERROR`), criterion `confirmed`, cases signed, not `human_judgement` | `CANDIDATE_DEFECT` |
| Product origin otherwise | `SPEC_GAP` |

Generic family nodes count as confirmed and signed: their claims need no requirement.

### 3. Seeded variants (`_seeded`, white-box)

For each seeded flag (`bug:<FLAG>`) that is switched on, the tests of the criteria it targets must fail with a kill origin twice: in-process, and over the network on that variant's own stack. Only then is the seed `killed`.

### 4. Mutants (`_measure`, white-box)

1. **Pool.** The mutants come from three sources:
   - mutmut over `mutation.scope().mutate`;
   - **handler mutants** (`handlers.generate`): AST swaps of comparisons (`==`↔`!=`, `<`↔`>=`, …) and of field constraints (`ge`↔`gt`, `le`↔`lt`), plus size keywords, in the handlers the criteria cite;
   - **field mutants** on the branch's changed lines.

   Some handler mutants are sealed as *blind* (`blind.py`) and kept out of tuning; they are counted as `held_out_blind`.
2. **Linking.** One stats run, with the trampolines in place, records which mutated functions each passing test **reaches**. A test that fails only with the trampolines in place is `overlay_broken`.
3. **Matrix.** Each mutant runs only against the tests that reach it, with `MUTANT_UNDER_TEST=<name>` and a 15 s timeout per test.
4. **Cells** (`decide.kills`): `K` is a failure with a kill origin (product origins, `CONFORMANCE` or `TIMEOUT`). `S` is a setup failure.
5. **Cache.** The whole measurement is cached under `data/gate_cache/<sha256>.json`. The key covers the handlers, the tests and their lock hashes, the runner digest, the mutated sources, the version, k, the seeds and the gate's own code.

### 5. Admission (`decide.admit`)

| Condition | Status |
|---|---|
| Kills can be measured, 0 < kills < `min_kills`, no seeded kill | `WEAK` |
| Kills a mutant no other **unit** kills, or killed its seed | `ADMITTED` |
| Kills only mutants others also kill | `ADMITTED_REDUNDANT` |
| Nothing can be measured (black-box: `NoMutation`) | `ADMITTED_UNMEASURED` |
| Kills can be measured and it kills none | `WEAK` |

### 6. Survivors and equivalence

`decide.survivors` classifies every mutant no test killed:
- `test_gap`: within `near` lines of a criterion's code ref;
- `out_of_scope`;
- `unclassified`: no line known.

`ai.propose` suggests which survivors are **equivalent**, meaning they change nothing observable. Each suggestion becomes an `equivalent_mutant` review item showing the diff.

Mutation strength = killed ÷ (total − equivalent). It is computed for the whole pool, for each criterion, and for handler mutants only. On black-box targets it is `"not_measured"`.

## Output: `gate_report`

```
{admit: "v2", bound_tests, cases, settings, shuffle_seed, cache,
 rows[{node, unit, ac, case_id, stability: "STABLE@K5"|"FLAKY", outcome, origin, status,
       seeded?, misses_target, kills, unique_kills, linked_mutants, eventually_consistent, excluded?}],
 summary{STATUS: n}, criteria{ac: {statuses, reached_mutants, killed, equivalent, strength}},
 mutants{total, killed, equivalent, not_covered, strength}, handlers{…, held_out_blind},
 survivors[{mutant, file, line, class, criteria, covered, diff, equivalent}], proposals[],
 overlay_broken[], timings{stability, seeded, mutants}, uncovered_criteria[]}
```

`kill_matrix` holds `{mutants[{name, file, line, function}], rows[{node, ac, status}], cells{node: {mutant: K|S}}, csv, csv_path}`. The CSV is also written to `data/runs/gate-exec-<id>/kill_matrix.csv`.

On r-0002: ADMITTED 4 · ADMITTED_REDUNDANT 8 · CANDIDATE_DEFECT 9 · WEAK 15.

## Failure modes

- `no BOUND tests to gate`.
- A runner or stack failure inside a run surfaces as `ERROR` rows, not as a failed stage.

## Files and tests

Tests: `test_decide.py`, `test_mutants.py`, `test_handlers.py`, `test_stage.py`.
