# testplat guide: the end-to-end flow, stage by stage

This guide explains how a test run works, from a repo URL and a one-sentence requirement to a report and a fix that a retest proves. It covers the whole flow first and then each stage in turn: what goes in, what comes out, how the stage works, who decides, and what its results mean.

For installation, see [README.md](README.md). For the stored real runs, see [evidence/README.md](evidence/README.md).

---

## 1. The idea in one paragraph

A model **proposes**: requirements, test cases, test code, browser flows and fix suggestions. **Fixed code decides**: which tests can be trusted, what each result means, where a defect is, and whether a fix is proven. A **person decides what "correct" means**: they answer the open questions about the requirements, then sign off on the cases that will run. A verdict never depends on a model's opinion, so any verdict can be explained from its stored records.

---

## 2. The flow at a glance

```
 ┌──────────┐   once per system
 │ Discover │   repo URL → how to start, reach and log in        (AI + probes, person if stuck)
 └────┬─────┘
      ▼          then per test run
 ┌──────────┐  ┌──────┐  ┌─────────┐  ┌───────┐  ┌──────────┐  ┌──────────┐
 │  Input   │→ │ Spec │→ │ Clarify │→ │ Cases │→ │ Sign-off │→ │ Generate │
 │ (person) │  │ (AI) │  │(person) │  │AI+calc│  │ (person) │  │(AI+check)│
 └──────────┘  └──────┘  └─────────┘  └───────┘  └──────────┘  └────┬─────┘
                                                                   ▼
 ┌─────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌─────────┐  ┌──────┐  ┌──────┐
 │ Deliver │← │ Retest │← │ Report │← │ Triage │← │ Execute │← │ Env  │← │ Gate │
 │ (fixed) │  │(fixed) │  │(fixed) │  │(fixed) │  │ (fixed) │  │(fixed│  │(fixed│
 └─────────┘  └────────┘  └────────┘  └────────┘  └─────────┘  └──────┘  └──────┘
```

| # | Stage | Question it answers | Done by | Output artifact |
|---|---|---|---|---|
| 0 | Discover | How do I start, reach and log in to this system? | AI proposes, probes verify, a person fills gaps | `target_profile`, `discovery_probes` |
| 1 | Input | What should the system do? | person | `requirements`, `run_options` |
| 2 | Spec + Clarify | Exactly what are the rules, and which are settled? | AI drafts, a person answers | `spec`, `spec_md` |
| 3 | Cases + Sign-off | Which situations do we test, and with what expected values? | AI picks situations, calculators compute expected values, a person signs | `cases`, `signoff_md` |
| 4 | Generate | What test code runs each case? | AI writes the binding; static checks and a dry run accept it | `bound_tests`, `ledger` |
| 5 | Gate | Can each test be trusted? | fixed code | `gate_report`, `kill_matrix` |
| 6 | Env | Is the system up, isolated and fingerprinted? | fixed code | `env_spec` |
| 7 | Execute | What did the system actually do? | fixed code | `run_records` (sealed) |
| 8 | Triage | Why did each failure happen, and where? | fixed code (plus an advisory AI fix suggestion) | `triage` |
| 9 | Report | What can we claim, and what limits it? | fixed code | `report` |
| 10 | Retest | Is the fix proven? | fixed code | `retest` |
| 11 | Deliver | What goes back to the repo's owners? | fixed code | `delivery` |

**Technical reference, one document per stage** (in [`guide/`](guide/)):
[0 Discover](guide/00-discover.md) ·
[1 Input](guide/01-input.md) ·
[2 Spec + Clarify](guide/02-spec.md) ·
[3 Cases + Sign-off](guide/03-cases.md) ·
[4 Generate](guide/04-generate.md) ·
[5 Gate](guide/05-gate.md) ·
[6 Env](guide/06-env.md) ·
[7 Execute](guide/07-execute.md) ·
[8 Triage](guide/08-triage.md) ·
[9 Report](guide/09-report.md) ·
[10 Retest](guide/10-retest.md) ·
[11 Deliver](guide/11-deliver.md)

In the web UI, a run follows the same order: **Targets** (discover once) → **New run** (input) → **Runs** (follow the stages, answer anything under *Needs you*, read the report, retest).

---

## 3. How a run is held together

This machinery is shared by every stage, so knowing it makes the rest of the guide easier to follow.

- **A run is a series of stage executions.** The runner (`tp/core/runner.py`) runs the stages in a fixed order. A stage receives only the artifacts it declares under `consumes` and must produce the ones it declares under `produces`.
- **Artifacts are content-addressed.** Every output is stored as `{manifest, body}`, and its id is the sha256 of both (`tp/core/artifacts.py`). Changing one byte gives a different id. Each artifact records its parents, so you can trace any verdict back to the brief it came from.
- **Heads and staleness.** The *head* of an artifact type is the output of the latest successful execution that produced it. When you re-run a stage, everything downstream that consumed the old head is marked **stale** and re-runs.
- **Review items pause a run.** A stage that needs a person creates a review item and stops at `awaiting_review`. Answering the item (`tp review resolve <id> …`, or *Needs you* in the UI) resumes the same execution, which can now see the decision. Review item kinds are `setup`, `question`, `signoff`, `unbound`, `equivalent_mutant`, `triage` and `confirm_observed`.
- **How much a person is asked.** By default every review item waits. `--focused` waits only at Clarify, Sign-off and setup questions. `--unattended` waits for nothing. Auto-decided items use a fixed default: questions take the recommended answer, marked **assumed**; sign-off is deferred, so cases are labelled **unsigned**; unconfirmed code behaviour is left out of scope. The report lists who decided each item (see [guide/01-input.md](guide/01-input.md)).
- **Strategies hide the target.** The stages never name a target. They call `ctx.strategies` (reach, spec, auth, state, data, mutation), which resolves from the target's profile (`tp/core/strategies.py`). The template gets white-box strategies; a discovered repo gets black-box ones (`tp/stages/env/blackbox.py`).
- **Model calls are bounded.** All model calls go through one gateway (`tp/core/llm.py`) using `claude-haiku-5-5`. Every call is recorded with its prompt hash and cost, and appears on the run page.

### Three kinds of target

| Kind | How it's added | What you get |
|---|---|---|
| **The template** (full-stack-fastapi-template, branch `feature/item-pricing`) | built in, `targets/fastapi-template/` | White-box: code-cited requirements, planted mutants, file:line localisation, AI fix suggestion and FIXED_CONFIRMED |
| **A discovered repo** | `tp target add <repo-url>[@ref]` | Black-box: its whole API is in scope, generic checks plus your requirement, localisation at the API level |
| **A live system** | `tp target add <url> --live --secret token=…` | Black-box through a deny-by-default safe-mode proxy; only what the run created is changed or deleted (isolation 2) |

---

## 4. The stages

Each stage below is described the same way: **purpose**, **in → out**, **how it works**, **who decides**, **results** and **code**.

### Stage 0: Discover (once per system)

> **Technical reference:** [guide/00-discover.md](guide/00-discover.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Turn a repo URL into a *profile*: the facts the rest of the pipeline needs to start, reach and log in to the system. Nothing about a specific target is hard-coded; CI enforces that.

**In → out.** A repo URL, optionally followed by `@tag` or `@branch` → `target_profile` (also written to `data/targets/<slug>/profile.yaml`) plus `discovery_probes`, the sealed and redacted log of every probe.

**How it works.** Discovery runs as its own run, holding only this stage. For each concern it tries the cheapest source first and keeps a value only once a probe against the running system confirms it:

| Concern | What it settles | Typical source |
|---|---|---|
| **Ref** | Which commit is tested | The latest release tag (`^v?\d+(\.\d+)+$`); if there is none, it asks for `@<ref>` |
| **Reach** | How to start the system and which port it serves | A compose file (up to 3 variants tried) or a root Dockerfile with a numeric `EXPOSE` |
| **Spec** | The OpenAPI document | Convention paths (`/openapi.json`, `/api/schema/?format=json`, `/v3/api-docs`, `/swagger/v1/swagger.json`) or a spec file in the repo |
| **Auth** | How to log in and which token scheme to send | A spec operation matching `token\|login\|signin\|session`; `Bearer` or `Token`; an admin created from env vars |
| **Data** | A valid body for each collection's create call | Built from the spec, then probed with a create and a read-back |
| **Paging, IDs** | List conventions and id style | The spec plus probes |
| **Outside services** | Webhooks or email the app calls | AI proposes; a stand-in run verifies it |
| **UI flows** | Browser flows worth replaying | AI proposes from the page's accessibility snapshot; a replay verifies it |

Every field records its **source** (`repo`, `probed`, `resolver`, `ai_proposed+verified`, `clarify`, `override`), its **layer** (`convention`, `algorithm`, `exploration`) and the number of the probe that is its evidence. `--no-conventions` skips the framework conventions so you can see what exploration finds on its own.

**Who decides.** Probes decide. When nothing can be verified (a port written as `${VAR}`, an env value the app needs to start, a request body the server keeps refusing), discovery raises a **setup question** (at most 5 per run) instead of guessing. Answers go through the same probe as any other candidate.

**Results.** The Targets page shows counts of Verified, Clarify and Override fields, plus the layers. `tp target show <slug>` prints the profile.

**Code.** `tp/stages/discover/` (`discovery.py` holds the steps, `reach.py`, `ai.py`, `ui_flows.py`, `dependencies.py`) and `config/discovery.yaml` (the conventions).

---

### Stage 1: Input

> **Technical reference:** [guide/01-input.md](guide/01-input.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Capture what the feature should do.

**In → out.** A person's words → `requirements` and `run_options`.

**How it works.** You give one of:
- `--brief "…"`: one vague sentence, e.g. *"Items get an optional price and a discount percentage; responses show the discounted price."*
- `--story story.md`: a full user story with acceptance criteria.
- On black-box targets, routes to focus on (from the New run page).

`run_options` carries the run's settings: the target, `--unattended` / `--focused`, `--fresh` (reuse no earlier answers), and per-stage `--opt key=value` (for example the gate's `k`).

**Code.** `tp/core/runner.py` (`start_run`), `tp/cli/`, `web/src/pages/NewRun.tsx`.

---

### Stage 2: Spec, then Clarify

> **Technical reference:** [guide/02-spec.md](guide/02-spec.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Turn loose words into precise acceptance **criteria**, each citing where it came from, and get a person to settle anything that's still open.

**In → out.** `requirements` + `run_options` → `spec` (criteria plus decisions) and `spec_md` (a readable version).

**How it works.**
1. **Index what exists.** On the template it indexes the source code at the branch: routes, models and validators. On a black-box target it indexes the served OpenAPI, narrowed to the chosen routes.
2. **Draft.** The model drafts criteria (*given / when / then*). Each one cites its sources: a line of the requirement, a code file:line, or a spec operation.
3. **Check.** Fixed checks reject criteria with no source, sources that don't exist, or invented HTTP statuses.
4. **Ask.** Gaps become **questions**, at most 5 per round and 2 rounds. Kinds include `ambiguity`, `story_code_conflict`, `missing_error_path`, `rule_interaction`, `no_requirement` and `suspected_defect`. Every question offers fixed answers too: *"The current behaviour is intended"*, *"Leave to human judgement"* and *"Out of scope: don't test this"*.
5. **Integrate.** Answers become **decisions** (e.g. `round-half-up`, `allow 100 so final_price is 0`) and override the AI's draft. Earlier answers to the same question are reused unless you pass `--fresh`.

**Who decides.** A person, at Clarify. Each criterion ends **confirmed** (a person settled it), **assumed** (nobody did; unattended runs) or **observed** (it describes current behaviour).

**Code.** `tp/stages/spec/` (`code_index.py`, `openapi_index.py`, `questions.py`, `checks.py`).

---

### Stage 3: Cases, then Sign-off

> **Technical reference:** [guide/03-cases.md](guide/03-cases.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Pick concrete situations to test, and give each one an expected value the model did **not** invent.

**In → out.** `spec` → `cases` (ranked, abstract test cases) and `signoff_md`.

**How it works.**
1. **Techniques.** The cases are built from fixed templates, in this order: `error_guessing`, `decision_table`, `boundary`, `error_exception`, `acceptance`. The model may add at most 3 extra cases per criterion.
2. **Expected values come from calculators or from the criterion's text, never from the model.** The model chooses a situation, a named calculator and its inputs. Fixed code runs that calculator (for example `percent_off` or `page_window`) to get the expected value. Where the expected value is a literal, such as a status, it is taken from the criterion's own text. A **differential check** compares each calculator result with an independent property (e.g. a discounted price stays between 0 and the price). If the two disagree, the case is flagged for attention, and the drafted case is never trusted as it stands.
3. **Clean up.** Duplicates are removed, a budget is applied, and the cases are ranked by priority. An exit check makes sure every in-scope criterion has a case and that no case contradicts a Clarify answer.
4. **Preconditions** must name factories the target's data strategy provides. Anything else becomes a leading *Given …* step.
5. **Sign-off.** A person approves, rejects (with a comment, and the model revises the line) or excludes each line, over at most 3 rounds. The signed set is hashed; on r-0002, *"27/27 lines approved"* was signed as `e02ed73bdb46`.

**Who decides.** A person signs off on everything that will run. Fixed code supplies the expected values.

**Code.** `tp/stages/cases/` (`build.py`, `calculators/`, `templates/*.yaml`).

---

### Stage 4: Generate

> **Technical reference:** [guide/04-generate.md](guide/04-generate.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Turn each signed case into runnable test code, without letting the model touch what is asserted.

**In → out.** `cases` + `spec` → `bound_tests` (the test files and their hashes) and `ledger` (every attempt).

**How it works.**
1. **A scaffold per case unit.** Fixed code writes one pytest file per unit. The case data, the expected values and the assertions sit in a **locked** region, hashed as `lock_hash`.
2. **The model writes only the binding:** the few lines that make the request and set up data.
3. **Static checks** (an AST pass) reject the file if it:
   - imports anything beyond `pytest` and `platform_rt`;
   - uses time, randomness, `uuid`, `os` or `sys`;
   - uses `eval`, `exec`, `open` or `getattr`-style dynamics;
   - skips or xfails;
   - reassigns protected names;
   - changes the locked region.
4. **Dry run.** The test runs once in the runner container. A failure is classified as `locator`, `fixture` or `generation`, and the model gets up to **3 repairs**. A unit that still fails is `UNBOUND` and is raised as an `unbound` review item. It is never silently dropped.
5. **Generic check families** are added as one more bound file. They are fixed platform code with no model, read the served OpenAPI, and need no requirement:
   - `properties`: never a 5xx for a spec-valid request;
   - `auth_required`: never a 2xx without credentials on a secured operation;
   - `unknown_id`: never a 2xx for an id that doesn't exist;
   - `invalid_input`: invalid input is refused;
   - `read_back`: declared fields round-trip;
   - `delete_final`: a deleted thing is gone;
   - `paging`: lists page as the profile says;
   - `dependency_called`, `dependency_failure_handled`: added when a stand-in is wired.
6. **Browser flows** (`.ui.yaml`) from discovery are bound the same way and run in a pinned Chromium.

**Who decides.** The static checks and the dry run. The `bound_tests` hash is the identity every later stage, and the retest, refer to.

**Code.** `tp/stages/generate/` (`scaffold.py`, `checks.py`, `loop.py`, `suite.py`) and `platform_rt/platform_rt/families.py`.

---

### Stage 5: Gate

> **Technical reference:** [guide/05-gate.md](guide/05-gate.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Prove each test can fail, and for the reason it claims, **before** any of its results count.

**In → out.** `bound_tests` + `cases` + `spec` → `gate_report` (a status for each test) and `kill_matrix`.

**How it works.**
1. **Stability.** Every test runs **K = 5** times on the reference system (3 on a live system), plus one run in shuffled order with a recorded seed. A test whose outcome or failure origin changes between runs is **FLAKY**.
2. **Routing.** A stable test is routed by its outcome and the *origin* of its failure:

| Result on reference | Status |
|---|---|
| Unstable | `FLAKY` |
| Skipped (excluded by the profile, or no valid body) | `EXCLUDED` |
| Fails because the server disagrees with its own spec | `SPEC_MISMATCH`, never a defect |
| Fails in its own binding, conformance or data setup | `REJECTED_ORACLE` |
| Fails for environment or platform reasons | `ERROR` |
| Fails on the product, criterion confirmed, cases signed | `CANDIDATE_DEFECT` |
| Fails on the product, but the criterion is assumed or left to judgement | `SPEC_GAP` |
| Passes | goes on to admission ↓ |

3. **Admission by planted bugs** (white-box). Mutants are generated in the branch's diff (mutmut), and the seeded variants are run too. A test that catches a mutant no other unit catches is `ADMITTED`. One that catches only mutants others also catch is `ADMITTED_REDUNDANT`. One that catches none is `WEAK`. On black-box targets nothing can be planted, so passing tests are `ADMITTED_UNMEASURED`.
4. **Survivors.** A mutant nobody caught within 3 lines of a criterion's code reference is a **test gap**. If a mutant can't be caught because it changes nothing, a person marks it as an `equivalent_mutant`. Mutation strength is killed ÷ (total − equivalent).

**Who decides.** Fixed rules (`tp/stages/gate/decide.py`). The thresholds are run settings: `k` 2–10, `min_kills` 1–5 and `near` 0–20.

**Code.** `tp/stages/gate/` (`decide.py`, `mutants.py`, `blind.py`).

---

### Stage 6: Env

> **Technical reference:** [guide/06-env.md](guide/06-env.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Bring up the system under test, isolated, and fingerprint exactly what it is.

**In → out.** The target's strategies → `env_spec` (services, images, commit and a `spec_hash` fingerprint).

**How it works.**
- **Template:** its own compose stack, at the branch, with any named patch applied as a variant (`fixed:<patch>`).
- **Discovered repo:** the repo's compose file is **rewritten** before it runs:
  - published ports are removed;
  - every service joins an `internal: true` network with no outbound access;
  - one forwarder bound to `127.0.0.1` is the only way in;
  - images are built or retagged at the tested commit;
  - secrets are passed as `${TP_SECRET_*}` references, never as values;
  - memory and CPU limits are added.

  An unknown compose key, or a build that could reach the host, **fails closed**.
- **Outside services** (webhooks, email) point at local **stand-ins** that record what they receive and can inject faults. Results that rest on a stand-in are labelled *simulated*.
- **Live systems** are reached only through the safe-mode proxy, which denies by default.

**Isolation level** carried by every claim: **0** isolated · **1** partly isolated (stand-ins) · **2** a live system on this Mac that the platform didn't start.

**Code.** `tp/stages/env/` (`strategies.py` for the template, `blackbox.py` for discovered and live targets), `platform_rt/platform_rt/standin.py` and `proxy.py`.

---

### Stage 7: Execute

> **Technical reference:** [guide/07-execute.md](guide/07-execute.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Run every gated test against the real system, several times, and record everything.

**In → out.** `gate_report` + `bound_tests` + `env_spec` → `run_records`: a sealed folder `execute-N/`.

**How it works.**
1. Every gate row runs, apart from `EXCLUDED` ones. Its lane says why it runs: `main`, `main:candidate`, `main:spec_gap` or `main:weak`.
2. Each test runs **K = 3** attempts on the reference system and on each seeded variant.
3. The recorder captures every request, response, assertion and backend log line, linked by a trace header.
4. Each (test, variant) gets a verdict computed over its attempts:

| Verdict | Meaning |
|---|---|
| `PASS` | All attempts passed. It also records **detection power**: the highest failure rate a PASS at K can't rule out (0.63 at K = 3) |
| `CONSISTENT_FAIL` | Every attempt failed, the same way (same origin and assertions) |
| `INTERMITTENT` | Some attempts failed, or they failed in different ways |
| `TIMEOUT` | Every attempt timed out |
| `SUSPECT_ENV` | The environment wasn't healthy around this batch (e.g. a 429) |
| `NOT_RUN` | It couldn't run, with the reason recorded |

5. A test stable on the reference system but failing on a variant is re-run in an **observation lane** (10 runs) to separate a flaky test from a defect the variant introduces.
6. The folder is **sealed** with `records.sha256`, and the seal's hash goes into the artifact. Things the run created on a target it doesn't own are deleted newest-first. Anything that can't be deleted is listed.

**Who decides.** Fixed code (`tp/stages/execute/decide.py`). There is no model in this stage.

**Code.** `tp/stages/execute/`, `platform_rt/platform_rt/record.py`.

---

### Stage 8: Triage

> **Technical reference:** [guide/08-triage.md](guide/08-triage.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Give every non-passing verdict exactly one class with its evidence, and locate real defects.

**In → out.** `run_records` + `gate_report` + `cases` + `spec` → `triage` (findings and defects).

**How it works.**
1. **Refuse tampered records.** If any file no longer matches the seal, triage stops.
2. **Classify** with hard rules, in this order. The first rule that matches wins:

| Order | Class | When |
|---|---|---|
| 1 | `ENV` | `SUSPECT_ENV`, an environment `NOT_RUN`, an `ENV` origin, or the target only answered 429, 502, 503 or 504 |
| 2 | `DATA` | Setup or a factory failed |
| 3 | `FLAKY_TEST` / `INTERMITTENT_PRODUCT` | Intermittent: in the test's own code, or on the product side and confirmed by escalation |
| 4 | `TEST_DEFECT` | The test itself is at fault (binding, conformance, platform) |
| 5 | `SPEC_MISMATCH` | The server disagrees with its own spec |
| 6 | `SPEC_GAP` | The criterion behind it was never confirmed |
| 7 | `PRODUCT_DEFECT` | A confirmed criterion, a signed case, and a consistent product-side failure |
| — | `INSUFFICIENT_EVIDENCE` | No rule settles it |

3. **Localise** (white-box). A fixed chain runs from the failing assertion to its HTTP exchange, then the trace log line, the traceback, the innermost app frame, the handler's parameter, the clause a database error names, and the criterion's code references. The result is at most **5 suspects**, each with its basis. On r-0002, O-5 pointed to `backend/app/api/routes/items.py:62` and `backend/app/models.py:112`. On black-box targets, localisation stops at the API: the operation, the request, the failed assertion, the raw answer and the backend log.
4. **Suggest a fix** (white-box, advisory). This is the only AI step here. The model proposes find/replace edits. Fixed code accepts an edit only if its `find` occurs exactly once and the file still parses, then builds the diff itself. A suggestion counts for nothing until a retest proves it.
5. **Review.** Each real defect becomes a `triage` item. A person can *accept* the class, or *override* it with another class and a reason; the override is recorded with who made it.

**Code.** `tp/stages/triage/` (`rules.py`, `localise.py`, `propose.py`, `evidence.py`).

---

### Stage 9: Report

> **Technical reference:** [guide/09-report.md](guide/09-report.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Say what can be claimed, then its limits, then what it rests on, using stored artifacts only.

**In → out.** The run's artifacts → `report`: `report.json`, a single static `report.html`, `summary.md` and the CLI worklist.

**How it works.**
- **The first line** is a claim, its limits and its basis, for example:
  `flatnotes · container · isolation 0 (isolated) · Stable, property-checked · ran 27 of 31; blocked ▸ signed case not bound to a test 4/31 (13%) · rests on: real 27/27 (100%)`.
- **Priorities:**
  - **P0** defects: `PRODUCT_DEFECT` and `INTERMITTENT_PRODUCT`.
  - **P1** untested or weak: weak tests, signed cases with no test, and criteria with no admitted test.
  - **P2** test health: flaky and test-side findings, quarantined tests, redundant tests, and mutation strength ("not measured" on black-box targets).
  - **P3** spec drift: spec gaps, assumed criteria and server/spec disagreements.
- Every claim links to its evidence: the case, the criterion, the sealed exchange and the log line. Every resolved review decision in the run's lineage is listed with who made it.
- **Export** (an action, not a stage) writes the bound tests onto a branch of your repo.

**Who decides.** Fixed code. There is no model in this stage.

**Code.** `tp/stages/report/` (`build.py`, `render.py`, `export.py`).

---

### Stage 10: Retest

> **Technical reference:** [guide/10-retest.md](guide/10-retest.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Prove a fix: the same tests, on the fixed code, with nothing else broken.

**In → out.** `run_records` + `triage` + `bound_tests` + `gate_report` → `retest`.

**How it works.** You pick a defect and the variant to test against (`fixed:<patch>`, or your own fix). In the UI, that's **Try suggested fix** or **Retest my fix**. Unattended runs retest automatically.
1. **Same tests.** The source hash and `lock_hash` of each test must match the failing run's, or the retest is `RETEST_INVALID` and nothing runs.
2. **Same environment.** The fingerprints are compared. Anything beyond the app's own image and code, or a patch that was changed or removed, makes the retest inconclusive.
3. **Fail → pass.** The defect's tests run **K = 5** times on the fixed variant.
4. **Pass → pass.** Every test that passed before is re-checked, to catch regressions.
5. **Same contract.** The served API contract of every operation under test must be unchanged (`$ref`s resolved). A "fix" that changes the contract proves nothing.

| Verdict | When |
|---|---|
| `RETEST_INVALID` | The tests changed |
| `INCONCLUSIVE` | The defect wasn't reproduced before, the environment differs, a check couldn't be observed (`SUSPECT_ENV` / `NOT_RUN`), or the contract changed |
| `NOT_FIXED` | A defect test still fails |
| `FIXED_WITH_REGRESSION` | Fixed, but a test that passed before now fails |
| **`FIXED_CONFIRMED`** | Same hashes, failed before, passes every run after, no regressions, contract unchanged. **A defect closes only on this verdict** |

**Code.** `tp/stages/retest/` (`decide.py`, `feed.py`).

---

### Stage 11: Deliver

> **Technical reference:** [guide/11-deliver.md](guide/11-deliver.md): the contract, algorithm, decision rules, configuration, output schema and failure modes.

**Purpose.** Turn each real defect into something the repo's owners can act on.

**In → out.** `triage` + `retest` → `delivery`.

**How it works.** There is no model in this stage. A PR score and hard rules decide:
- A defect with a retest-proven fix, whose score is at or above the threshold, becomes a **draft PR** on a fork the platform owns. The PR holds the tests and the fix, with a disclosure that AI was used.
- Otherwise it becomes an **issue draft** with a `curl` reproduction and a Schemathesis command. A spec/server disagreement only ever becomes an issue.
- **Nothing leaves the machine** unless `TP_PUBLISH=1`. By default, drafts are written locally under the run.

**Code.** `tp/stages/deliver/stage.py`.

---

## 5. Beyond one system: domain packs

A **domain pack** (`packs/<name>/pack.yaml`) holds one vendored spec with its hash, a brief, settled answers, and several implementations pinned to commits.

```bash
uv run tp pack run realworld        # full pipeline on the first implementation
uv run tp compare r-NNNN r-MMMM     # the case × implementation matrix
```

The first implementation goes through the whole pipeline. The others run **the same sealed tests** (the same `bound_tests` hash). Each disagreement is then judged:
- **The witness rule.** If one implementation passes a check, the expectation can be met, so another implementation failing that check is a defect.
- **Disagreement classes:** `AGREE`, `SPEC_MISMATCH`, `TO_PERSON` (a person must judge) and `INCOMPLETE`.

On RealWorld, aspnetcore answered 201 to a spec-valid article, while express-bookshelf answered 500 to the same body. That is a real defect.

---

## 6. Two worked examples

### White-box: the template branch (r-0002, attended)

| Stage | What happened |
|---|---|
| Input | *"Items get an optional price and a discount percentage; responses show the discounted price."* |
| Spec + Clarify | 13 criteria, all confirmed; 8 decisions, e.g. round half up, and a 100% discount is allowed so the final price is 0 |
| Cases + Sign-off | 27 cases with calculator-computed prices; 27/27 lines approved |
| Generate | The bound test files, hash `69aed466…` |
| Gate | K = 5 plus a shuffled run, then mutants: ADMITTED 4 · ADMITTED_REDUNDANT 8 · CANDIDATE_DEFECT 9 · WEAK 15 |
| Execute, Triage | 6 PRODUCT_DEFECT. O-5 (rounding) located to `items.py:62` and `models.py:112` |
| Report | "P0 defects: 6" |
| Retest | O-5 **FIXED_CONFIRMED**: 3/3 fail before, 5/5 pass after, 27 passing tests re-checked, 0 regressions |

The full records are in `evidence/runs/r-0002-template-real-defect/`.

### Black-box: a small notes app discovered from its URL (flatnotes)

| Stage | What happened |
|---|---|
| Discover | Dockerfile reach, `/openapi.json`, `POST /api/token` login with a Bearer token. Three setup questions were answered: the port (the Dockerfile says `EXPOSE ${FLATNOTES_PORT}`), the app's env credentials, and a valid note body |
| Run (unattended, about 7 minutes) | Isolation 0; 27 of 31 checks ran (4 signed cases weren't bound to a test); everything rests on the real system |
| Triage | 3 real P0s, located at the API: `POST /api/notes` without `content` → 500; `POST /api/token` with a non-ASCII username → 500; `GET /api/search?limit=0` → 500 (also a server/spec disagreement) |

---

## 7. Driving it

| You want to | CLI | Web UI |
|---|---|---|
| Add a system | `tp target add <url>[@ref]` | Targets → Discover |
| See its profile | `tp target show <slug>` | Targets → click the name |
| Give it a secret | `tp target secret <slug> key=value` | — |
| Start a run | `tp run --target <slug> --brief "…"` (or `--story file.md`) | New run |
| Answer what's waiting | `tp review list`, then `tp review resolve <id> …` | Run page → *Needs you* |
| Re-run one stage | `tp stage run <stage> …` | — |
| Compare implementations | `tp compare r-A r-B` | — |
| Clean up a live target | `tp target clean <slug>` | — |
| Check the stored evidence | `uv run python evidence/verify.py` | — |

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Criterion** | One precise rule from the spec stage, with its sources; it is confirmed, assumed or observed |
| **Case** | One situation to test, with an expected value from a calculator |
| **Binding** | The only model-written part of a test: how the request is made |
| **Origin** | Where a failure came from: `ASSERTION`, `CONTRACT`, `PRODUCT_ERROR`, `SPEC`, `BINDING`, `CONFORMANCE`, `DATA`, `ENV`, `PLATFORM` or `TIMEOUT` |
| **Variant** | A version of the system: `reference`, a seeded defect, or `fixed:<patch>` |
| **Seeded** | A defect planted on purpose, and labelled as such |
| **Stand-in** | A local replacement for an outside service; results that rest on one are *simulated* |
| **Isolation 0 / 1 / 2** | Isolated / stand-ins / a live local system |
| **Sealed** | Write-once, with a hash over every file, so any change is detected |
| **Witness** | An implementation that passes a check, proving the expectation can be met |
