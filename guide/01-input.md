# Stage 1: Input (technical reference)

[← Guide](../GUIDE.md) · previous: [Discover](00-discover.md) · next: [Spec + Clarify](02-spec.md)

| | |
|---|---|
| Module | `tp/core/runner.py` (`start_run`), `tp/cli/main.py` (`tp run`), `tp/api/` (`POST /runs`), `web/src/pages/NewRun.tsx` |
| Execution | A synthetic `input` stage execution, recorded as `succeeded` when the run is created |
| Produces | `requirements` (optional), `run_options` (always) |
| Model | none |
| Person | writes the brief or story |

## Purpose

Input fixes everything that belongs to the run itself rather than to a stage: what the feature should do, which target it runs on, and how much a person stays in the loop. There's no stage class for it. `start_run` stores the run's inputs as an already-succeeded `input` execution, so the runner's normal head resolution passes them to the stages like any other artifact.

## What `start_run` does

1. It validates the mode (`live | record | replay`) and the input types. Only `requirements` and `run_options` are allowed (`RUN_INPUTS`).
2. It stores `run_options` as an artifact. `{}` is the default, and `unattended: true` is merged in when the flag is given.
3. In one transaction, it inserts the `pipeline_runs` row, the `input` execution with its outputs, and one `pending` execution for each stage in `PIPELINE`, or in the run's own pipeline (`["discover"]` for `tp target add`).
4. It pins the target, and refuses to start if another run holds the same target (`target busy: r-NNNN holds <slug>`).
5. It emits `run_created` and `run_target`. Then `runner.advance(run_id)` drives the stages until the run finishes or a review item has to wait.

## `requirements`

```json
{"kind": "brief", "text": "Items get an optional price and a discount percentage; responses show the discounted price."}
{"kind": "story", "text": "<the markdown of a full user story>"}
```

From the CLI, use `--brief "…"` or `--story file.md`, not both. Domain packs (`tp pack run`) and `tp watch` write a `brief` from the pack or scan.

## `run_options`

Stages read only the keys they know. The keys in use:

| Key | Read by | Meaning |
|---|---|---|
| `target` / `profile` | runner, discover | Target slug or folder (`--target`) |
| `unattended` | runner, generate, retest | Apply the default decision to every review item |
| `stops` | runner | The review kinds that still wait (`--focused` sets `question, confirm_observed, setup, signoff`) |
| `fresh` | spec, cases | Reuse no earlier answers or sign-offs |
| `routes` | spec, cases | Narrow the spec to these operations (white-box: also their imports and the diff) |
| `rules` | spec (`clarify`), cases (`cases`) | Extra rules from New run, e.g. "money rounds half up" |
| `flows` | cases | The flows in scope (default `FLOW-items-crud` on the template) |
| `templates` | cases | Restrict which case techniques may be used |
| `iteration` | cases | A second cycle fed from a report's gaps (`tp retest feed`) |
| `guards`, `variant`, `units`, `max_repairs` | generate | Repair policy `v1`/`v2`, variant, a subset of units, and the repair cap |
| `admit`, `gate{k, min_kills, near, every_criterion}` | gate | Admission version and its thresholds |
| `k`, `variants` | execute | Attempts per test and the variants to run |
| `defect`, `after` | retest | The defect to retest and the variant with the fix |
| `no_conventions`, `live`, `keep_up`, `answers` | discover | Discovery mode and pre-settled answers |

Each stage's own option can also be passed generically: `--opt key=value`.

## The three levels of attention

The runner decides what happens when a stage returns `awaiting_review` (`runner.advance`, `review.UNATTENDED_DECISION`):

| Mode | What waits for a person | What happens to the rest |
|---|---|---|
| attended (default) | every review item | — |
| `--focused` | `question`, `confirm_observed`, `setup`, `signoff` | auto-decided, recorded as `by: focused` |
| `--unattended` | nothing | auto-decided, recorded as `by: unattended` |

The automatic decisions:

| Kind | Decision | Effect |
|---|---|---|
| `signoff` | `defer` | Cases are unsigned and labelled `unsigned` |
| `question` | `defer` | The platform's recommended answer is used, marked `assumed` |
| `confirm_observed` | `out_of_scope` | Code-derived behaviour no one confirmed never reaches cases |
| `unbound` | `accept_gap` | The unbindable case is listed as a gap |
| `equivalent_mutant` | `not_equivalent` | The mutant counts as a survivor |
| `triage` | `accept` | The rules' class stands |

Any other kind fails an unattended run.

## Failure modes

| Error | Cause |
|---|---|
| `not a run input: [...]` | An input type other than `requirements` or `run_options` |
| `target busy: r-NNNN holds <slug>` | One run per target at a time |
| `give --story or --brief, not both` | Both were given |
