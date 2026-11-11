# Stage 2: Spec, then Clarify (technical reference)

[← Guide](../GUIDE.md) · previous: [Input](01-input.md) · next: [Cases + Sign-off](03-cases.md)

| | |
|---|---|
| Module | `tp/stages/spec/stage.py` (`SpecStage`) |
| Version | `1.0.0+p<prompt hash>`: any prompt change bumps the version |
| Consumes | `requirements`, `run_options` |
| Produces | `spec`, `spec_md` |
| Model | `claude-haiku-5-5`: extract, investigate and write questions (effort `medium`) |
| Person | `question` (Clarify), `confirm_observed` |
| Events | `spec_focused`, `spec_investigated`, `spec_answer_reused`, `spec_question_settled`, `spec_frozen` |

## Purpose

This stage turns loose requirement text into **criteria**: precise *given / when / then* rules, each citing its source. A person then settles everything that is ambiguous. The frozen spec is the single definition of "correct" that every later stage uses.

## Algorithm

The stage is resumable. Its working state lives in `data/runs/spec-exec-<id>.json` between review rounds.

### Round 1 (`_round1`)

1. **Index what exists.**
   - **White-box** (the profile's spec has a `source_dir`): `code_index.build` indexes the source files at the ref, giving routes, models and validators with file:line. With `routes`, `code_index.focus` keeps only the chosen routes, their imports and the branch diff.
   - **Black-box**: `openapi_index.build` indexes the served OpenAPI, narrowed to `routes`.
2. **Extract.** `ai.extract` drafts criteria from the requirement text. `checks.check_extracted` keeps a criterion only if its quoted source text is found in the requirements and its `title`, `when` and `then` are present. The rest go to `rejected`.
3. **Lint.** `checks.lint_all` tags each criterion with the ambiguity types it shows: `vague_quantifier`, `missing_value`, `no_observable_outcome`, `undefined_term`, `missing_actor`, `missing_error_path`, `unspecified_boundary`, `conflicting_statements` (the same `when` with different outcomes) and `implicit_assumption`. A criterion with a **blocking** tag (`vague_quantifier`, `missing_value`, `no_observable_outcome`, `undefined_term`, `unspecified_boundary` or `conflicting_statements`) starts as `draft`. Any other starts as `confirmed`.
4. **Investigate.** `ai.investigate` reads the numbered source or spec against the criteria. It returns three things:
   - `criterion_routes`: which routes each criterion touches, kept only if the route exists;
   - `observed`: behaviour the code shows that the requirements don't mention, which becomes `O-n` criteria with status `observed` and is kept only if its evidence file:line exists;
   - `findings`: suspected defects, kept only with valid evidence.
5. **Reverse trace.** Routes that no criterion covers go into `unmapped_routes`.
6. **Question candidates.** `questions.candidates` builds questions from the model's questions and the criteria.

### Asking (`_ask`)

`questions.filter_` picks at most **5 questions per round** (`PER_ROUND`), over at most **2 rounds** (`ROUNDS`). Each question has a key: a sha256 of the criterion title and its subject.

- **Reused.** A question answered before, in the same scope, is applied without asking (`spec_answer_reused`). The scope is the SUT ref and the requirements text, plus the routes and rules if given. `--fresh` turns reuse off.
- **Settled by an earlier answer.** If a decision made in this run already covers the question, the recommended answer is assumed (`spec_question_settled`).
- **Asked.** Otherwise a `question` review item is created. It carries the model-written text, its choices, a recommendation, and three fixed choices:
  - *The current behaviour is intended*
  - *Leave to human judgement*
  - *Out of scope: don't test this*

  Every question can also be answered with **Other: describe what should happen**.
- **Confirm observed.** Each `observed` criterion that isn't a suspected defect gets a `confirm_observed` item (`confirm` / `reject` / `out_of_scope`).

### Integrating answers (`_integrate`)

| Decision | Effect on the criterion |
|---|---|
| `question → pick` | `apply_answer` rewrites the field the question changes (e.g. the expected value). The decision is recorded as `{key, about, answer, assumed: false}` |
| `question → pick "Other"` | The reviewer's own text is the answer |
| `question → defer` | The recommendation is used (or *Leave to human judgement* if there is none), with `assumed: true` |
| *Leave to human judgement* | `human_judgement: true`. The gate later routes failures to `SPEC_GAP`, never to a defect |
| *Out of scope* | `status: out_of_scope` |
| `confirm_observed → confirm` | `status: confirmed` |
| `confirm_observed → reject` | `suspected_defect: true`, plus a round-2 follow-up: "what should happen?" |
| `confirm_observed → out_of_scope` | `status: out_of_scope` |

`questions.contradictions` flags answers that conflict with one another (`review_needed: true`). Every change is appended to `history` with a version number. Only the answer is stored, not who gave it (that stays in the review log), so the same answers always give the same spec hash.

### Freezing (`_finish`)

- Criteria still `draft` or `observed` once the rounds are used up become `assumed` with `review_needed: true`. A suspected defect also becomes `human_judgement`.
- `checks.exit_check` requires every criterion to have a known status and at least one source. It also requires an expected outcome (`then` or `status_code`), unless the criterion is `human_judgement` or `out_of_scope`. Any problem fails the stage: `exit check: …`.

## Criterion statuses

| Status | Meaning | Later effect |
|---|---|---|
| `confirmed` | A person settled it, or the text was unambiguous | A failure can be a `CANDIDATE_DEFECT` |
| `assumed` | Nobody settled it | A failure is a `SPEC_GAP`; the report lists it under P3 |
| `observed` | Code-derived, not yet confirmed | Must be confirmed, or becomes assumed at freeze |
| `out_of_scope` | Excluded | No cases |
| `draft` | Blocked by ambiguity | Not allowed at freeze |

## Output: `spec`

```
{kind, requirements, sut{name, ref}, version, index{routes, files[, from: "openapi"]},
 criteria[{id: "C-n"|"O-n", title, given, when, then, status_code?, status, sources[{kind, file?, line?, quote?}],
           routes[], ambiguity[], human_judgement?, suspected_defect, review_needed}],
 findings[], unmapped_routes[], rejected[], decisions[{key, about, answer, assumed, reused?, settled_by?}],
 history[{version, changes}], contradictions[]}
```

`spec_md` is the same content rendered as markdown, for reading at Clarify.

## For the UI

`routes()`, `suggest_rules()` and `default_rules()` feed the New run screen's route picker and rule suggestions.

## Files and tests

- `ai.py`: prompts and schemas.
- `checks.py`: extraction checks, lint and exit check.
- `questions.py`: filter, phrase, apply and contradictions.
- `code_index.py`, `openapi_index.py`.
- `render.py`.
- `cli.py`.
- Tests: `tests/test_spec.py`.
