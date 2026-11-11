# testplat

**An AI test platform that finds real defects and proves its claims.** Give it a repo URL (or the URL of a system that's already running) and a requirement in one sentence. It works out how to start, reach and log in to the system, turns the requirement into tests, runs them against the real system, and reports defects with their evidence. A fix counts only once a retest proves it.

**The principle:** AI proposes, fixed code decides, and a person decides what "correct" means. A model drafts requirements, test cases, test code, browser flows and fix suggestions. Fixed code decides which tests are trusted, what each result means, where a defect is, and whether a fix is proven.

**Who it's for:** a developer or tester with a service or a feature branch, and requirements in words, who wants to know whether it does what was asked, and wants evidence rather than a green tick.

---

## What it can do

| Capability | How |
|---|---|
| **Requirement → tests → verdict** | A one-sentence brief becomes requirements that cite the code, then test cases, then test code. **Expected values come from fixed calculators, never from the model.** A person settles what "correct" means and signs off on what will run |
| **Any repo of the right shape, from its URL** | `tp target add <url>` discovers how to start the system (compose or Dockerfile), its OpenAPI spec and base path, its login and token scheme, its outside services and its UI flows. Every field records how it was found, and its evidence. There's no target code, and CI enforces that |
| **Tests that transfer within a domain** | A domain pack (one spec, pinned implementations) runs the full pipeline on one implementation, then **the same sealed tests** on the others. The **witness rule** turns a disagreement into a defect: if one implementation passes, the expectation can be met |
| **Beyond pytest and the backend** | Browser flows (`.ui.yaml`): the AI proposes them from the page's accessibility snapshot, a run verifies them, and fixed code replays them in a pinned Chromium with an API read-back. Any new format plugs in through a collector keyed by file suffix |
| **Beyond the container** | Outside services (webhooks, email) are replaced by **local stand-ins**, and the platform tests how the system uses them, including when they fail. Live systems are reached through a **deny-by-default proxy**. Every result carries an **isolation level** (0 / 1 / 2) and says what it **rests on** (real or simulated) |
| **Results you can trust** | A test is admitted only after K stable runs, a shuffled run, and catching planted bugs. Every exchange is recorded and sealed. An environment failure is never a product defect. A fix is FIXED_CONFIRMED only with the same test hashes, a fail before, a pass on every run after, no regressions and an unchanged API contract |

## Results on real systems

All of these ship in [`evidence/`](evidence/README.md), stage by stage, as the platform wrote them.

| System | Language | Result | Run |
|---|---|---|---|
| full-stack-fastapi-template, branch `feature/item-pricing` | Python | From a one-sentence brief: 27 signed cases, 6 defects. A real rounding defect was located to file and line, and its fix proven **FIXED_CONFIRMED** (3/3 fail before, 5/5 pass after, 0 regressions) | r-0002 |
| RealWorld: aspnetcore and express-bookshelf | C#, JavaScript | The same sealed tests on both: AGREE 29 · SPEC_MISMATCH 5 · TO_PERSON 6. **A real defect:** express-bookshelf answers 500 to a spec-valid article where aspnetcore answers 201 | r-0079 → r-0080 |
| Memos | Go | From a one-line brief, 37 of 42 planned checks ran. A browser flow proposed by AI was verified, then passed 3/3. A webhook stand-in received the call | r-0055 |
| The notify fixture (built here, with **seeded** defects) | Python | Four seeded defects in outside-service behaviour and the UI, each caught and retested to **FIXED_CONFIRMED** | r-0061 |
| Paperless-ngx, live | Python | A system the platform didn't start: isolation 2, safe mode, 21 of 35 checks run, 0 leftovers | r-0046 |
| The template, with a broken stack | — | An environment failure classed ENV: 62 findings, **0 false defects** | r-0001 |

```bash
uv run python evidence/verify.py
# 130 artifacts, 15 sealed run folders (15 against a recorded root)
# all intact
```

---

## Spin it up

### Requirements

| | Version |
|---|---|
| Python, uv | 3.14, uv 0.12 (`pyproject.toml`, `uv.lock`) |
| Docker | Docker Desktop 27, compose v2 (for real runs; a few GB of disk for target images) |
| Node | 23 (for the web UI only) |
| Model | `claude-haiku-5-5`: an `ANTHROPIC_API_KEY` for real runs (the template run r-0002 cost $0.041) |
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

### 3. Test a repo from its URL

```bash
uv run tp target add https://github.com/usememos/memos       # discovery: how to start, reach and log in
uv run tp run --target memos --brief "People write short notes, then edit and delete them."
```

If discovery needs a value it can't work out (a port, an env value), it asks:

```bash
uv run tp review list
uv run tp review resolve <id> --text "<answer>"
```

### 4. Run a domain pack

```bash
uv run tp pack run realworld          # full pipeline on the first implementation, sealed tests on the rest
uv run tp compare r-NNNN r-MMMM       # the case × implementation matrix, each disagreement judged
```

### 5. Test a live system you already run

```bash
uv run tp target add http://localhost:8010 --live --secret token=$TOKEN
uv run tp run --target localhost-8010
uv run tp target clean localhost-8010   # remove anything the run created
```

### 6. Open the web UI

```bash
uv run tp serve                       # API on :8000
cd web && npm ci && npm run dev       # UI on http://localhost:5173
```

Runs, the run page (each step, every AI call and its cost, the report, retest buttons) and each target's profile.

**Notes**
- Without `--unattended`, a run stops for a person at Clarify and Sign-off (in the UI, or with `tp review`).
- Runs with Docker take minutes. On a Mac, wrap them in `caffeinate -i`. One run at a time.
- Data lives in `data/` (git-ignored; override with `TP_DATA`). Nothing is published unless `TP_PUBLISH=1`.

---

## How it works

```
input → discover → spec → clarify → cases → sign-off → generate → gate → env → execute → triage → report → retest → deliver
        AI+probes   AI     person    AI+calc   person    AI+checks  fixed  fixed  fixed     fixed    fixed    fixed    fixed
```

| Stage | What it does | Code |
|---|---|---|
| Discover | Reach, spec, auth, outside services and UI flows; each field with its source and evidence | `tp/stages/discover/` |
| Spec, clarify | Requirements that cite the code; a person settles the gaps | `tp/stages/spec/` |
| Cases, sign-off | Test design; expected values from calculators; a person signs off on everything that will run | `tp/stages/cases/` |
| Generate | Test code (pytest, `.ui.yaml`), accepted by static checks and a dry run | `tp/stages/generate/`, `platform_rt/collect.py` |
| Gate | K stable runs, a shuffled run, planted bugs | `tp/stages/gate/` |
| Env, execute | The system on an internal network with stand-ins; K attempts; every exchange sealed | `tp/stages/env/`, `tp/stages/execute/`, `platform_rt/` |
| Triage, report | Failure class and location from the raw records; the report leads with its claim, limits and basis | `tp/stages/triage/`, `tp/stages/report/` |
| Retest, compare | The same sealed tests on a fix, or on another implementation | `tp/stages/retest/`, `tp/core/transfer.py` |

| Folder | Holds |
|---|---|
| `tp/core/` | The stage contract, the run state machine, content-addressed artifacts, reviews, labels, the model gateway |
| `platform_rt/` | The runner library: recorder, HTTP client, spec-derived checks, stand-in, browser interpreter, safe-mode proxy |
| `tp/api/`, `tp/cli/`, `web/` | The HTTP API, the `tp` command, the React UI |
| `packs/`, `targets/`, `config/` | Domain packs, the template's profile, discovery and safety rules |
| `evidence/` | Real runs, stage by stage, with `verify.py` |

A walkthrough of one run, stage by stage, is in [`evidence/README.md`](evidence/README.md).

### Extending it

| To add | It takes |
|---|---|
| A target of the same shape | `tp target add <url>`, and answers to any setup questions. No code |
| A domain | A `packs/<name>/pack.yaml`: a vendored spec with its hash, a brief, settled answers, implementations pinned to commits |
| A test format | A collector keyed by file suffix in `platform_rt/collect.py` |
| A kind of outside service | A stand-in and its wiring rule. It counts once it catches a seeded defect |

**Where tests transfer next** (predicted, scored 0–10 on a shared spec, maintained implementations, a conformance suite, HTTP/JSON fit and onboarding effort): FHIR R4 8.5 (next) · RealWorld 8 (built) · the Servarr family 7 · OCI distribution 6.5 · Matrix 6.5.

---

## Design notes

### Two architecture decisions

| | A model is used only when proposing | Local containment at the network edge |
|---|---|---|
| Decision | What a model proposes (cases, UI flows, dependency wiring) is frozen into data, verified by running, and replayed. No model acts during a run | The system's outbound traffic reaches only local stand-ins on an internal network. Traffic to a live system goes through the safe-mode proxy |
| Instead of | Agents acting at run time; a model as the judge | In-process mocks; record and replay; real third parties |
| Why | A verdict must be reproducible and explainable from its records | Some systems can't be fully isolated; this keeps them local and labels what's simulated |
| Trade-off | A UI change means proposing the flow again | Simulated parts are labelled; no remote level |
| Code | `tp/stages/discover/ui_flows.py`, `platform_rt/platform_rt/ui.py`, `tp/stages/gate/` | `tp/stages/env/blackbox.py`, `platform_rt/platform_rt/standin.py`, `platform_rt/platform_rt/proxy.py` |

### A design iteration: the report's first line

- **Before:** `Strongest claim: Seen on the live system, stable 3/3 · strategies: reach=external, spec=served, auth=token_header, …`
- **After:** `localhost-8010 · external · isolation 2 (live and local) · Seen on the live system, stable 3/3 · ran 21 of 35; blocked ▸ excluded: NOT_RUN 14/35 (40%) · rests on: real 21/21 (100%)`

The old line gave a claim and then strategy names. A reader couldn't tell which system it was, how isolated the run was, how much of the plan ran, or whether the results rested on the real system. The new line gives the claim, then its limits, then its basis. It heads the run page, the report and the summary (r-0046, report exec #332 versus #348).

### A failure, handled

The first RealWorld comparison showed express-bookshelf failing 12 cases that aspnetcore passed. The sealed records held 70 answers of 429: the app allows 20 sign-ins per 15 minutes (`lib/users/router.js:9-11`). Now a failed attempt that met a 429 is SUSPECT_ENV, an environment problem, so it's left out of the comparison rather than counted as a disagreement (`314b482`, with a test). The matrix went from AGREE 20 / TO_PERSON 22 to AGREE 29 / TO_PERSON 6.

### Corrections where AI was involved

- **In the product:** answers given at Clarify override the AI's drafts, and a fixed check flags any case that contradicts one. A model's proposal (a UI flow, a stand-in setting) is kept only once a run verifies it. A retest "fix" that passed only by changing the API contract led to the contract guard.
- **In the build:** real targets exposed about 25 platform bugs (an id inside an envelope, a sign-up taken for the login, `$ref` bodies not followed, …). Each was fixed in shared code, with a test that fails without the fix. A false P0 on RealWorld came from judging the two implementations against different specs; both now use the pack's one spec.

### Labels

| Label | Meaning |
|---|---|
| **real** / **simulated** | The assertion ran against the real system / rested on a local stand-in |
| **isolation 0 / 1 / 2** | Isolated / partly isolated (stand-ins) / live and local |
| **AI-proposed (+verified)** | A value a model proposed, and whether running verified it |
| **assumed** | A criterion no person confirmed (unattended runs) |
| **seeded** | A defect planted on purpose (the notify fixture, the template's patches) |
| **pre-generated** | From an imported test suite (none in use) |
| **screen-recorded playback** | In demo material: a recording of a real run, not a live one |

### Known limits

- Everything runs on this machine; there is no remote level.
- On black-box targets, localisation is at the API level, plus the handler file where the source is available. Data isn't reset between attempts, so some rows are INTERMITTENT (labelled, never counted as defects).
- Cases that need a second user stay unbound on black-box targets (9 of 55 on RealWorld).
- The RealWorld fix passes 5/5 with 0 regressions, but its retest is INCONCLUSIVE: the app's sign-in rate limit kept one check from being observed.

### Sources

| Reused as-is | Modified | Own work |
|---|---|---|
| FastAPI, Typer, Pydantic, PyYAML, uvicorn, SQLite; React, TanStack Query/Router, Vite | The template's clone: a test-info endpoint and trace-header propagation (the platform's copy only) | The pipeline, stage contract and artifact store |
| pytest, Docker Compose, mutmut, Playwright, Mailpit | A target's compose file, rewritten onto an internal network with stand-ins and a forwarder | Discovery; the gate, sealed records, triage, report and retest |
| The RealWorld OpenAPI spec (MIT, vendored) | | `platform_rt`: checks, stand-ins, the `.ui.yaml` interpreter, the proxy |
| Targets: Memos, Paperless-ngx, the RealWorld implementations | | Domain packs, sealed transfer and the witness rule; the web UI |
| Claude Haiku 5.5 (in the product); Claude Code (the build assistant) | | |

**Most valuable contribution:** the proof rules. When a generated test may be trusted (the gate), when a disagreement is a defect (the witness rule), and when a fix is proven (same sealed hashes, a fail before, a pass on every run after, no regressions, an unchanged contract).
