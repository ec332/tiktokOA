# testplat

**An AI test platform that finds real defects and proves its claims.** You give it a repo URL (or the URL of a system that's already running) and a one-sentence requirement. It works out how to start the system, reach it and log in. It turns the requirement into tests and runs them against the real system. Defects are reported with their evidence. A fix counts only once a retest proves it.

**The principle:** AI proposes, fixed code decides, and a person decides what "correct" means.

**Who it's for:** a developer or tester who has a service or a feature branch and a requirement in words. They want to know whether it does what was asked, with evidence rather than a green tick.

| | In one line | Section |
|---|---|---|
| **How it's built** | Twelve stages joined by a typed, content-addressed contract. Models only propose; every verdict is fixed code | [Architecture](#1-architecture) |
| **What you use** | A web UI (Targets → New run → Run) and a CLI with the same steps. A person is asked at two points only | [Product and UI](#2-product-and-ui) |
| **How far it goes** | 6 real systems in 4 languages, no target code, $0.02–0.05 of model calls per run | [Real systems, completeness and scale](#3-real-systems-completeness-and-scale) |
| **Why you can believe it** | Tests are admitted only after proof, records are sealed, and every number shows what it counts | [Results you can trust and read](#4-results-you-can-trust-and-read) |
| **How it flows** | URL → profile → brief → signed cases → report → proven fix; 7–17 minutes per run | [The workflow, end to end](#5-the-workflow-end-to-end) |

The full walkthrough is in **[GUIDE.md](GUIDE.md)**. There is one technical reference per stage in **[guide/](guide/)**, and the real runs, stage by stage, are in **[evidence/](evidence/README.md)**.

---

## 1. Architecture

### The pipeline

```
discover → input → spec → clarify → cases → sign-off → generate → gate → env → execute → triage → report → retest → deliver
AI+probes  person   AI    person   AI+calc   person    AI+checks  fixed  fixed   fixed    fixed    fixed    fixed    fixed
```

| Stage | What it decides | Who decides | Reference |
|---|---|---|---|
| Discover | How to start, reach and log in to a system it has never seen | Probes verify what AI and conventions propose; a person fills gaps | [00](guide/00-discover.md) |
| Spec + Clarify | The exact rules, each citing its source | AI drafts, a person settles | [02](guide/02-spec.md) |
| Cases + Sign-off | The situations to test and their expected values | Calculators give the values, a person signs | [03](guide/03-cases.md) |
| Generate | Test code | AI writes only the request; static checks and a dry run accept it | [04](guide/04-generate.md) |
| Gate | Which tests may be trusted | Fixed rules: K stable runs, a shuffled run, planted bugs | [05](guide/05-gate.md) |
| Env + Execute | What the system actually did | Fixed code; isolated network; K attempts; sealed records | [06](guide/06-env.md), [07](guide/07-execute.md) |
| Triage | Why each failure happened, and where | Ordered hard rules; AI fix suggestion is advisory | [08](guide/08-triage.md) |
| Report | What can be claimed | Fixed code, from stored artifacts only | [09](guide/09-report.md) |
| Retest | Whether a fix is proven | Fixed rules: same tests, same environment, same contract | [10](guide/10-retest.md) |
| Deliver | What goes back to the repo's owners | A score and hard rules; nothing published unless `TP_PUBLISH=1` | [11](guide/11-deliver.md) |

### The core design

- **One stage contract.** Each stage declares what it `consumes` and what it `produces`, both typed and versioned. The runner (`tp/core/runner.py`) gives a stage only the artifacts it asked for. A new stage plugs in without touching the others.
- **Content-addressed artifacts.** Every output's id is the sha256 of its manifest and body (`tp/core/artifacts.py`). Each artifact records its parents, so any verdict traces back to the sentence it came from. When a stage is re-run, everything downstream of the old output is marked stale.
- **One review queue.** Every human decision is a typed review item: `question`, `signoff`, `setup`, `triage`, and so on. A stage that needs a person pauses, and resumes in the same execution once the decision is made (`tp/core/review.py`).
- **Targets behind strategies.** Stages never name a target. They call `ctx.strategies`: reach, spec, auth, state, data and mutation. These resolve from the target's profile to a white-box set (the template) or a black-box set (any discovered repo). CI fails if target names appear in platform code (`tests/test_target_literals.py`).
- **One model gateway.** Every model call goes through `tp/core/llm.py` with `claude-haiku-5-5`. Each call is recorded with its prompt hash and cost. Prompts are part of a stage's version, so changing a prompt changes the version.

### Two architecture decisions

| | A model is used only when proposing | Local containment at the network edge |
|---|---|---|
| Decision | What a model proposes (cases, UI flows, dependency wiring) is frozen into data, verified by running, and replayed. No model acts during a run | The system's outbound traffic reaches only local stand-ins on an internal network. Traffic to a live system goes through the safe-mode proxy |
| Instead of | Agents acting at run time; a model as the judge | In-process mocks; record and replay; real third parties |
| Why | A verdict must be reproducible and explainable from its records | Some systems can't be fully isolated; this keeps them local and labels what's simulated |
| Trade-off | A UI change means proposing the flow again | Simulated parts are labelled; no remote level |
| Code | `tp/stages/discover/ui_flows.py`, `platform_rt/platform_rt/ui.py`, `tp/stages/gate/` | `tp/stages/env/blackbox.py`, `platform_rt/platform_rt/standin.py`, `platform_rt/platform_rt/proxy.py` |

### Code map

| Folder | Holds |
|---|---|
| `tp/core/` | The stage contract, the run state machine, content-addressed artifacts, reviews, labels, the model gateway |
| `tp/stages/<stage>/` | One folder per stage, each with its own tests |
| `platform_rt/` | The runner library: recorder, HTTP client, spec-derived checks, stand-ins, browser interpreter, safe-mode proxy |
| `tp/api/`, `tp/cli/`, `web/` | The HTTP API (with live events), the `tp` command, the React UI |
| `packs/`, `targets/`, `config/` | Domain packs, the template's profile, discovery conventions, sandbox and safety rules |
| `evidence/` | Real runs, stage by stage, with `verify.py` |

---

## 2. Product and UI

### The job it does

The question a developer brings is: *"does my branch do what was asked?"* The answer is a short claim, its limits, and the evidence for it, rather than a pass count. The platform does the routine work: reading the code, designing cases, writing and proving tests, running them, and classifying failures. It asks a person only for what can't be automated, which is what "correct" means.

### The screens

The web UI is React with TanStack Query and Router. It follows the order you work in:

| Screen | What you do there |
|---|---|
| **Targets** | Paste a repo URL and click **Discover**. Each target's profile lists every field with its source (probed, repo, AI-proposed + verified, a person's answer) and the probe that is its evidence |
| **New run** | Pick a target, then describe the work as a feature (a brief), a full story, or chosen endpoints. Add rules if you want ("money rounds half up") and click **Start run** |
| **Run** | One page from start to finish, updated live (server-sent events). Its sections: the **claim line**, five steps (understand the feature, clarify, design and approve tests, build and check, report), a **Needs you** panel for anything waiting on you, every AI call with its cost, **Confirm defect** / **Not a bug**, **Try suggested fix** / **Retest my fix**, **Export tests…** to your repo, and **Cancel run** / **Resume** |
| **Runs** | Every test run started from New run, newest first, with its status |

Each page has a **How this page works** panel that explains it in place. The CLI (`tp run`, `tp review`, `tp target`) offers the same steps, and so do the API (`tp serve`) and the generated OpenAPI client.

### Product decisions

| Decision | Why |
|---|---|
| **A person is asked at two points only: Clarify and Sign-off** (plus setup questions when discovery can't verify something) | That's where judgement is needed; everything else is decided by rules. `--focused` stops only there, and `--unattended` stops nowhere but labels what was assumed |
| **Questions come with choices and a recommendation**, including *"the current behaviour is intended"*, *"leave to human judgement"* and *"out of scope"* | Answering takes seconds, and each answer has a defined effect on what counts as a defect |
| **Sign-off is line by line** (approve / reject with a comment / exclude), with earlier approvals carried forward | You sign exactly what will run, and you're never asked twice for the same line |
| **The claim comes first** | See the design iteration below |
| **Every problem has a plain-language reason** ("Passes, but no code change the gate made was caught by it: it may check too little") | A tester can act on it without knowing the gate's internals |
| **The tests are yours to keep.** Export writes the trusted tests to a new branch of your repo, and Deliver drafts a PR or issue | The value outlives the run |

### A design iteration: the report's first line

- **Before:** `Strongest claim: Seen on the live system, stable 3/3 · strategies: reach=external, spec=served, auth=token_header, …`
- **After:** `localhost-8010 · external · isolation 2 (live and local) · Seen on the live system, stable 3/3 · ran 21 of 35; blocked ▸ excluded: NOT_RUN 14/35 (40%) · rests on: real 21/21 (100%)`

The old line gave a claim and then strategy names. A reader couldn't tell which system it was about, how isolated the run was, how much of the plan ran, or whether the results rested on the real system. The new line gives the claim, then its limits, then its basis. The same function writes it for the run page, the report and the summary (r-0046, report exec #332 versus #348).

---

## 3. Real systems, completeness and scale

### Results on real systems

| System | Language | Result | Run |
|---|---|---|---|
| full-stack-fastapi-template, branch `feature/item-pricing` | Python | From a one-sentence brief: 27 signed cases and 6 defects. A real rounding defect was located to file and line, and its fix proven **FIXED_CONFIRMED** (3/3 fail before, 5/5 pass after, 0 regressions) | r-0002 |
| RealWorld: aspnetcore and express-bookshelf | C#, JavaScript | The same sealed tests on both: AGREE 29 · SPEC_MISMATCH 5 · TO_PERSON 6. **A real defect:** express-bookshelf answers 500 to a spec-valid article where aspnetcore answers 201 | r-0079 → r-0080 |
| Memos | Go | From a one-line brief, 37 of 42 planned checks ran. A browser flow proposed by AI was verified, then passed 3/3. A webhook stand-in received the call | r-0055 |
| flatnotes, discovered from its URL | Python | Three setup answers, then unattended: 27 of 31 checks ran, and 3 real 500s found (a note without content, a non-ASCII login, `limit=0`). Records are in `data/`, not shipped | r-0084 |
| The notify fixture (built here, with **seeded** defects) | Python | Four seeded defects in outside-service behaviour and the UI, each caught and retested to **FIXED_CONFIRMED** | r-0061 |
| Paperless-ngx, live | Python | A system the platform didn't start: isolation 2, safe mode, 21 of 35 checks run, 0 leftovers | r-0046 |
| The template, with a broken stack | — | An environment failure classed ENV: 62 findings, **0 false defects** | r-0001 |

### How complete it is

- **The whole loop:** discovery → requirements → cases → tests → proof → execution → classification → report → proven fix → delivery. Each stage is a real implementation, not a stub, and each has its own tests.
- **Beyond pytest and the backend:**
  - **Browser flows** (`.ui.yaml`) are proposed from the page's accessibility snapshot, verified, and replayed in a pinned Chromium, with an API read-back.
  - **Outside services** (webhooks, email) are replaced by stand-ins that record what they receive and can inject failures.
  - **Live systems** run behind a deny-by-default proxy.
- **Generic checks need no requirement:** never a 5xx, never a 2xx without credentials, never a 2xx for an unknown or deleted id, and declared fields round-trip. These run on every target alongside the requirement's own tests.
- **The platform's own tests:** 559 tests (`uv run pytest -m "not docker"`), plus Docker-marked integration tests.

### How it scales

| Dimension | What it takes | Evidence |
|---|---|---|
| **A new system** | `tp target add <url>`, and answers to any setup questions. No code | flatnotes: 3 answers. aspnetcore: 1 (the spec) |
| **A new domain** | One `packs/<name>/pack.yaml`: a vendored spec with its hash, a brief, settled answers, implementations pinned to commits. The full pipeline runs once, then **the same sealed tests** run on every other implementation | RealWorld: 2 implementations, one bound-test hash |
| **A new test format** | A collector keyed by file suffix in `platform_rt/collect.py` | `.ui.yaml` |
| **A new kind of outside service** | A stand-in and its wiring rule. It counts once it catches a seeded defect | webhook, email |
| **Cost** | Model calls only where proposing: $0.041 (r-0002), $0.051 (r-0079), $0.025 (r-0055), $0.020 (r-0084), all with `claude-haiku-5-5` | `llm_cost` events |
| **Time** | 7 min (flatnotes, unattended), 10 min (Memos), 13 min (template, including the person's answers), 17 min (RealWorld) | run event timestamps |
| **Repeat runs** | Earlier answers and sign-offs are reused, and gate measurements are cached by content hash | `--fresh` turns reuse off |

**Where tests transfer next** (predicted). Each candidate is scored 0–10 on five things: a shared spec, maintained implementations, a conformance suite, HTTP/JSON fit and onboarding effort. FHIR R4 8.5 (next) · RealWorld 8 (built) · the Servarr family 7 · OCI distribution 6.5 · Matrix 6.5.

### What fits best, and the limits

**Best fit:** an HTTP JSON API that starts from a compose file or Dockerfile, serves an OpenAPI spec, has a username/password login, and has resources you can create and read back.

**Limits:**
- Everything runs on this machine. There is no remote level, and one run per target at a time.
- On black-box targets, localisation is at the API level, plus the handler file where the source is available. File-and-line localisation and AI fix suggestions are white-box only.
- Cases that need a second user stay unbound on black-box targets (9 of 55 on RealWorld).
- Not supported yet: OAuth/SSO logins, GraphQL- or gRPC-only APIs, and apps without an OpenAPI spec (unless one is supplied).
- The RealWorld fix passes 5/5 with 0 regressions, but its retest is INCONCLUSIVE: the app's sign-in rate limit kept one check from being observed.

---

## 4. Results you can trust and read

### The proof rules

| Claim | Rule that must hold |
|---|---|
| **A test may be trusted** | Stable over K runs and a shuffled run. Where code can be mutated, it catches a planted bug no other test catches. Tests that are flaky, weak or wrong in their own setup are routed out and named |
| **A failure is a product defect** | It fails on every attempt, on the product's side, against a criterion a person confirmed and a case a person signed. Environment trouble (a failed probe, a 429, a gateway error) is never a defect |
| **A disagreement is a defect** (domain packs) | The **witness rule**: if one implementation passes a check, the expectation can be met |
| **A fix is proven** | **FIXED_CONFIRMED** only with the same test hashes, a fail before, a pass on every run after, no regressions, and an unchanged API contract |

### Why the results are readable

- **Claim, limits, basis** head every run (see above).
- **Every number shows what it counts.** It is a count with its denominator and the ids behind it. The report refuses to build if any number lacks that breakdown.
- **Every defect has its evidence:** the request, the response, the failed assertion, and the backend log line, linked by trace id. White-box defects also have up to 5 suspect file:lines, each with its basis.
- **The worklist is in priority order:**
  - **P0:** defects.
  - **P1:** untested or weak.
  - **P2:** test health.
  - **P3:** spec drift.
- **Expected values never come from the model.** Fixed calculators compute them, or they are quoted from the criterion's own text. Where a property exists, it cross-checks the calculator.

### Labels

| Label | Meaning |
|---|---|
| **real** / **simulated** | The assertion ran against the real system / rested on a local stand-in |
| **isolation 0 / 1 / 2** | Isolated / partly isolated (stand-ins) / live and local |
| **AI-proposed (+verified)** | A value a model proposed, and whether running verified it |
| **assumed** | A criterion no person confirmed (unattended runs) |
| **unsigned** | Cases no person signed (unattended runs) |
| **seeded** | A defect planted on purpose (the notify fixture, the template's patches) |
| **pre-generated** | From an imported test suite (none in use) |
| **screen-recorded playback** | In demo material: a recording of a real run, not a live one |

### You can check it

Every cited run ships in [`evidence/`](evidence/README.md), copied unchanged from the platform's store:

```bash
uv run python evidence/verify.py
# 130 artifacts, 15 sealed run folders (15 against a recorded root)
# all intact
```

### A failure, handled

The first RealWorld comparison showed express-bookshelf failing 12 cases that aspnetcore passed. The sealed records held 70 answers of 429: the app allows 20 sign-ins per 15 minutes (`lib/users/router.js:9-11`). Now a failed attempt that met a 429 is SUSPECT_ENV (an environment problem), so it's left out of the comparison rather than counted as a disagreement (`314b482`, with a test). The matrix went from AGREE 20 / TO_PERSON 22 to AGREE 29 / TO_PERSON 6.

### Corrections where AI was involved

- **In the product:**
  - Answers given at Clarify override the AI's drafts, and a fixed check flags any case that contradicts one.
  - A model's proposal (a UI flow, a stand-in setting) is kept only once a run verifies it.
  - A retest "fix" that passed only by changing the API contract led to the contract guard.
- **In the build:**
  - Real targets exposed about 25 platform bugs: an id inside an envelope, a sign-up taken for the login, `$ref` bodies not followed, … Each was fixed in shared code, with a test that fails without the fix.
  - A false P0 on RealWorld came from judging the two implementations against different specs. Both now use the pack's one spec.

---

## 5. The workflow, end to end

```
 Targets: paste a URL ─▶ Discover ─▶ (answer a setup question if asked) ─▶ profile
 New run: pick the target, describe the feature ─▶ Start run
 Run:     Clarify (you) ─▶ Sign-off (you) ─▶ build, prove, execute, triage (automatic) ─▶ report
          ─▶ Try suggested fix / Retest my fix ─▶ FIXED_CONFIRMED ─▶ Export tests / draft PR
```

### Requirements

| | Version |
|---|---|
| Python, uv | 3.14, uv 0.12 (`pyproject.toml`, `uv.lock`) |
| Docker | Docker Desktop 27, compose v2 (for real runs; a few GB of disk for target images) |
| Node | 23 (for the web UI only) |
| Model | `claude-haiku-5-5`: an `ANTHROPIC_API_KEY` for real runs |
| Pinned images | Playwright 1.64.1 with Chromium (browser runner), Mailpit v1.33.1 (email stand-in) |

### 1. Install and check (no Docker, no model)

```bash
git clone https://github.com/ec332/tiktokOA && cd tiktokOA
uv sync
uv run pytest -m "not docker"            # the platform's tests
uv run tp run --stub --unattended        # the whole pipeline with stub stages
uv run python evidence/verify.py         # the shipped real runs are intact
```

### 2. Add your key

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-…' > .env
```

### 3. Open the web UI

```bash
uv run tp serve                       # API on :8000
cd web && npm ci && npm run dev       # UI on http://localhost:5173
```

### 4. Test a repo from its URL

In the UI: **Targets → Discover**, then **New run**. From the CLI:

```bash
uv run tp target add https://github.com/usememos/memos       # discovery: how to start, reach and log in
uv run tp run --target memos --brief "People write short notes, then edit and delete them."
uv run tp review list                                        # anything waiting for you
uv run tp review resolve <id> …
```

### 5. Run a domain pack

```bash
uv run tp pack run realworld          # full pipeline on the first implementation, sealed tests on the rest
uv run tp compare r-NNNN r-MMMM       # the case × implementation matrix, each disagreement judged
```

### 6. Test a live system you already run

```bash
uv run tp target add http://localhost:8010 --live --secret token=$TOKEN
uv run tp run --target localhost-8010
uv run tp target clean localhost-8010   # remove anything the run created
```

**Notes**
- Without `--unattended`, a run stops for a person at Clarify and Sign-off (in the UI, or with `tp review`). `--focused` stops only there.
- Runs with Docker take minutes. On a Mac, wrap them in `caffeinate -i`. One run per target at a time.
- Data lives in `data/` (git-ignored; override with `TP_DATA`). Nothing is published unless `TP_PUBLISH=1`.

---

## Sources

| Reused as-is | Modified | Own work |
|---|---|---|
| FastAPI, Typer, Pydantic, PyYAML, uvicorn, SQLite; React, TanStack Query/Router, Vite | The template's clone: a test-info endpoint and trace-header propagation (the platform's copy only) | The pipeline, stage contract and artifact store |
| pytest, Docker Compose, mutmut, Playwright, Mailpit | A target's compose file, rewritten onto an internal network with stand-ins and a forwarder | Discovery; the gate, sealed records, triage, report and retest |
| The RealWorld OpenAPI spec (MIT, vendored) | | `platform_rt`: checks, stand-ins, the `.ui.yaml` interpreter, the proxy |
| Targets: Memos, Paperless-ngx, the RealWorld implementations, flatnotes | | Domain packs, sealed transfer and the witness rule; the web UI |
| Claude Haiku 5.5 (in the product); Claude Code (the build assistant) | | |

**Most valuable contribution:** the proof rules. They decide when a generated test may be trusted (the gate), when a disagreement is a defect (the witness rule), and when a fix is proven (the same sealed hashes, a fail before, a pass on every run after, no regressions, an unchanged contract).
