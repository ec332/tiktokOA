# testplat

An AI test platform. Give it a repo URL (or the URL of a system that is already running) and a requirement in words. It works out how to start, reach and log in to the system, turns the requirement into tests, runs them against the real system, and reports defects with their evidence. A fix counts only once a retest proves it.

Requirement IDs (CR1, SR4, …) refer to `docs/iteration3/requirements.md`. Where each one is shown: [`docs/brief-evidence.md`](docs/brief-evidence.md).

## 1. Who it serves, the problem, and where it stops (SR1)

**Users:** a developer or tester with a feature branch or a service, and requirements written in words, who wants to know whether it does what was asked.

**The problem:** an AI can write many tests fast, but a generated test can be wrong, flaky, or run against a broken environment, and a rerun that passes proves nothing. The platform lets AI do the understanding and the drafting, and keeps every decision about what counts as evidence in fixed code: a test is admitted only after it runs stably and catches planted bugs; a verdict says what it rests on; a fix is confirmed only by the same sealed tests failing before and passing every time after.

**What it covers:**
- HTTP services you can start from a repo (compose file or Dockerfile) or reach on this Mac: API tests, generic checks from the OpenAPI spec, and browser flows.
- Outside services the system calls (a webhook, email), replaced by local stand-ins.
- One spec with several implementations (a domain pack): tests made on one run unchanged on the others, and the disagreements are judged.

**Where it stops:** everything runs on this Mac (no remote hosts, no publishing by default); one sample per run with Claude Haiku 5.5; one run at a time. The known limits are in §4.

## 2. Run it (SR3)

| | Version |
|---|---|
| Source | `ec332/tiktokOA`, branch `main` (the submitted version is the commit you check out) |
| Python, uv | 3.14, uv 0.12 (`pyproject.toml`, `uv.lock`) |
| Runner library | `platform_rt` 0.11.6 (built into the runner images) |
| Docker | Docker Desktop 27 (compose v2) |
| Web UI | Node, React 19, Vite 8 (`web/package.json`) |
| Model | `claude-haiku-5-5` (needs `ANTHROPIC_API_KEY` in `.env`) |
| Pinned images | Playwright 1.64.1 with its Chromium (browser runner); Mailpit v1.33.1 (email stand-in) |
| RealWorld pack | spec at upstream commit `25588846`, sha256 in `packs/realworld/pack.yaml`; implementations pinned to commits |

```bash
uv sync
uv run pytest -m "not docker"                  # platform tests, no Docker (CI runs this)
uv run tp run --stub --unattended              # the whole pipeline with stub stages, no Docker, no model

# a new target, from its repo URL
uv run tp target add https://github.com/usememos/memos      # discovery: how to start, reach, log in
uv run tp run --target memos --brief "People write short notes, then edit and delete them."

# a domain pack: the full pipeline on one implementation, the same sealed tests on the other
uv run tp target add https://github.com/realworld-apps/aspnetcore-realworld-example-app
uv run tp run --target aspnetcore-realworld-example-app --brief "<the pack's brief>" --unattended
uv run tp pack run realworld --first r-NNNN    # runs the sealed tests on express-bookshelf, prints the matrix

uv run tp serve                                # API on :8000
cd web && npm install && npm run dev           # UI on http://localhost:5173
```

Without `--unattended`, a run stops at the reviews (Clarify, Approve, Sign-off) for a person: `tp review list`, `tp review resolve <id> approve`, or the web UI. Long Docker runs take minutes; on a Mac, wrap them in `caffeinate -i`.

## 3. How it works (SR5)

```
input (repo URL or live URL, brief)
  → discover   how to start, reach and log in; stand-ins; UI flows       AI proposes, probes verify
  → spec       requirements, each citing the code or the brief           AI, checked
  → clarify    what "correct" means                                      person
  → cases      test design; expected values from fixed calculators       AI + fixed code
  → sign-off   everything that will run                                  person
  → generate   test code (pytest, .ui.yaml)                              AI, static checks + dry run
  → gate       K stable runs, a shuffled run, planted bugs               fixed code
  → env        the system on an internal network, stand-ins              fixed code
  → execute    K attempts over HTTP, every exchange recorded and sealed  fixed code
  → triage     class, localisation, fix suggestion                       fixed code (+ AI suggestion, labelled)
  → report     claim, limits, basis                                      fixed code
  → retest     same sealed tests on the fixed build                      fixed code
```

| Module | Responsibility |
|---|---|
| `tp/core` | runs, stage contract, artifact store (content-hashed), events, reviews, target profiles |
| `tp/stages/<stage>` | one folder per stage above; each consumes and produces typed artifacts |
| `tp/stages/discover` | the target profile (`data/targets/<slug>/profile.yaml`): every field with its source, layer (convention / algorithm / exploration / clarify) and probe evidence |
| `platform_rt` | the library inside the runner images: records, generic check families, stand-ins, the browser flow interpreter, the safe-mode proxy |
| `packs/<domain>` | a domain pack: one spec, a brief, settled answers, implementations pinned to commits |
| `tp/api`, `web/` | the HTTP API and the web UI (runs, reviews, targets, reports) |

**Key state:** a run is a chain of stage executions; each output is an artifact addressed by its hash, so a retest can prove it used the same tests. Execute writes a sealed run folder (`data/runs/execute-N/`: `results.jsonl`, `records.sha256`, logs), which the report and retest read.

**Two architecture decisions** (`docs/iteration3/decisions.md`):

| | DR-1: a model is used only when proposing | DR-2: local containment at the network edge |
|---|---|---|
| Decision | What a model proposes (cases, UI flows, dependency wiring) is frozen into data, verified by running, and replayed. No model acts during a test run | The system's outbound traffic reaches only local stand-ins, on an internal Docker network. The runner's traffic to a live system goes through a safe-mode proxy |
| Instead of | Agents that act at run time (browser agents, self-healing locators); a model as the judge | In-process mocks; recording and replaying real traffic; real third parties |
| Why | A verdict must be reproducible and explainable from records; a model in the loop makes the same test give different answers (CR4) | Some systems can't be fully isolated (FB-c); this keeps them on the Mac and labels what is simulated (U1, G3) |
| Cost | Less adaptive: a UI change means proposing the flow again | Simulated, and labelled; no remote level |
| In the code | `tp/stages/discover/ui_flows.py` (propose, then `verify` by running); `platform_rt/platform_rt/ui.py` replays the frozen steps; `tp/stages/gate` admits a test only after K runs | `tp/stages/env/blackbox.py` (internal run network, inbound-only forwarder); `platform_rt/platform_rt/standin.py`; `tp/stages/discover/dependencies.py` (finds and verifies the wiring); `platform_rt/platform_rt/proxy.py` (safe mode) |

## 4. Labels and known limits (SR4, G3)

Every result in a report carries:

| Label | Meaning |
|---|---|
| **real** | the assertion ran against the real system |
| **simulated** | it rests on a local stand-in (a webhook receiver, a mail server), not the real third party |
| **pre-generated** | from an imported test suite (none in use: the Hurl suite was cut) |
| **isolation 0 / 1 / 2** | 0 = isolated; 1 = partly isolated (stand-ins); 2 = live and local (a system the platform didn't start) |
| **AI-proposed** | in the target profile: a value a model proposed, and whether running verified it (`ai_proposed+verified`) |
| **assumed** | a criterion no person confirmed (unattended runs) |

In the demo material, **screen-recorded playback** marks a recording of a real run, not a live one. Seeded defects are labelled as seeded (the notify fixture, the template's planted-bug patches).

**Known limits:**
- No remote level: everything runs on this Mac.
- On black-box targets, localisation is at the API level (operation, request, failed assertion), plus the handler file where the source is there.
- Black-box targets keep data between attempts (no reset), so some tests are INTERMITTENT; they are labelled and never counted as defects.
- No factory for a second user or other prepared data on black-box targets: 9 of 55 RealWorld cases stay unbound.
- A wrong test can still be reported as a defect on a single target; with two implementations, the witness rule sends it to a person.
- A target's rate limit makes its attempts SUSPECT_ENV, so those cases are incomplete, not judged.
- Images-only targets (Memos, Paperless) can't host seeded defects; only targets built from source can.
- A RealWorld defect (express-bookshelf answers 500 to spec-valid article bodies, confirmed by a person) was localised and fixed, and the fix passes 5/5 with no regressions in 31 checks. But the retest is INCONCLUSIVE, not FIXED_CONFIRMED: the app's hard-coded sign-in rate limit kept one check from being observed after the fix (`docs/status/acceptance/003`). The FIXED_CONFIRMED evidence for CR3 is the template's real defect (r-0002) and the fixture's seeded ones (r-0061).

## 5. Sources (SR2)

| Reused as-is | Modified | Own work |
|---|---|---|
| FastAPI, Typer, Pydantic, PyYAML, uvicorn, SQLite; React, TanStack Query/Router, Vite | The template's clone (full-stack-fastapi-template): a test-info endpoint and trace-header propagation, in the platform's copy only | The pipeline, stage contract and artifact store |
| pytest, Docker Compose, mutmut | A target's compose file: rewritten onto an internal network, with stand-ins and a forwarder | Discovery: profiles with per-field provenance, verified by probing |
| Playwright + Chromium (browser runner), Mailpit (email stand-in) | | The gate (stability, shuffle, planted bugs), execute's sealed records, triage, report, retest |
| The RealWorld OpenAPI spec (MIT, vendored in `packs/realworld/spec`) | | `platform_rt`: check families, stand-ins, the `.ui.yaml` interpreter, the safe-mode proxy |
| Targets: Memos, Paperless-ngx, the two RealWorld implementations | | Domain packs, `tp compare` and the witness rule |
| Claude Haiku 5.5 (in the product); Claude Code (the build assistant) | | The web UI |

**Most valuable contribution:** the proof rules: when a generated test may be trusted (the gate), when a disagreement is a defect (the witness rule), and when a fix is proven (same sealed hashes, fail before, pass every time after, no regressions, unchanged contract).

## 6. A design iteration: the report's first line (SR6)

The same run (r-0046, a live Paperless reached only by URL and token), before and after:

- **Before** (report exec #332): `Strongest claim: Seen on the live system, stable 3/3 · strategies: reach=external, spec=served, auth=token_header, data=spec_derived, state=namespace, mutation=none, safety=safe_mode, checks=pytest_cases`
- **After** (exec #348): `localhost-8010 · external · isolation 2 (live and local) · Seen on the live system, stable 3/3 · ran 21 of 35; blocked ▸ excluded: NOT_RUN 14/35 (40%) · rests on: real 21/21 (100%)`

**Why:** the old line gave the claim and then a list of strategy names. A reader couldn't tell which system it was, how isolated the run was, how much of the plan ran and why the rest didn't, or whether results rested on the real system or a stand-in. The new line states the claim, then its limits, then its basis. The strategy detail moved below. The same line heads the run page in the UI.

## 7. Corrections where AI was involved (DM4)

**In the product (the platform's own model calls):**
- Clarify answers override the AI's drafts; a fixed check flags any case that contradicts an answer (r-0001 → r-0002: the rounding test used the wrong mode).
- A model's proposal is never trusted until running verifies it: a UI flow was repaired once from the page snapshot where it failed (Memos r-0053); a setting Haiku proposed for the webhook allowlist was kept only after a run showed it worked (r-0051).
- A retest "fix" that passed only by changing the API contract (r-0047, O-12) led to the contract guard (DR-10).

**In the build (the coding assistant), found on real targets and fixed in shared code with a test:**
- A false P0 on RealWorld came from judging the two implementations against different specs; both now use the pack's one spec (integration/006).
- Twenty-odd platform bugs exposed by Memos, the fixture and RealWorld (an envelope id, a sign-up taken as the login, `$ref` bodies not followed, …), each fixed with a test that fails without it (integration/002, 004, 006).
- Three commits once went in on a truncated test run; commits are now gated on the suite's last line (integration/004 §5).
- The assistant once restarted the API mid-run; the standing rule is now to check for active runs first.

## 8. One issue, end to end (DM5)

**A rate limit read as product failures.** The first RealWorld comparison (r-0073 × r-0074) showed express-bookshelf failing 12 cases that aspnetcore passed.

- **Located:** the sealed records of r-0074 held 70 answers of 429 inside those 12 cases, many on sign-ins rather than the request under test. The app limits sign-ins to 20 per 15 minutes per IP (`lib/users/router.js:9-11`). Triage only looked at the request under test, so the 429s never showed.
- **Fixed:** a failed attempt that met a 429 anywhere (with no injected fault) is SUSPECT_ENV, an environment problem: triage classes it ENV and `tp compare` counts it as missing, not as a disagreement (`tp/stages/execute/stage.py`, `throttled`; commit `314b482`, with a test).
- **Result:** r-0079 × r-0080: 6 answers of 429 in 2 cases, both now INCOMPLETE; the comparison moved from AGREE 20 / TO_PERSON 22 to AGREE 29 / TO_PERSON 6 (with the one-spec fix above). Records: `data/runs/execute-545/`.
- **Remaining limit:** those 2 cases are not judged on express-bookshelf at all. A run that paced its sign-ins could judge them.

## 9. What adding the next one takes (U2)

| To add | It takes |
|---|---|
| A target with the same shape (OpenAPI, a compose or Dockerfile app, JSON bodies, a token login) | `tp target add <repo URL>`; answer the setup questions discovery can't settle. No target code (`tests/test_target_literals.py` bans target names in the platform) |
| A domain | a `packs/<name>/pack.yaml`: a vendored spec with its hash, a brief, settled answers, implementations pinned to commits. Then `tp pack run <name>` |
| A test format | a collector keyed by file suffix in `platform_rt/platform_rt/collect.py` that writes the same per-test record; the gate, records, triage and retest apply unchanged (DR-4). `.ui.yaml` is the example |
| A dependency kind | a stand-in in `platform_rt/platform_rt/standin.py` (or a pinned image, like Mailpit), and its wiring in `tp/stages/discover/dependencies.py`; it must catch a seeded defect in the fixture before it counts (DR-8) |

Next domain, costed as a prediction: FHIR (`docs/iteration3/catalog.md`).

## 10. Earlier demo (iteration 2)

Rehearsed end to end on 2026-11-08, one sample, Claude Haiku 5.5. Evidence: `docs/status/demo/`.

| # | Command | Run | Result |
|---|---|---|---|
| 1 | `tp target add https://github.com/paperless-ngx/paperless-ngx@v3.4.2` → `tp run --target paperless-ngx` | r-0039, r-0040 | Served spec, declared auth, admin from documented env vars; 18 fields verified, 0 overrides. Ceiling **"Stable, property-checked"** |
| 2 | `tp target add http://localhost:8010 --live --secret token=$TOKEN` → `tp run --target localhost-8010` → `tp target clean` | r-0041, r-0046 | A Paperless the platform didn't start and can't control. Safe mode, ceiling **"Seen on the live system"**, 0 leftovers. **Correction:** at the time, an absolute-URL request could leave through the proxy with the token. Iteration 3 refuses those, along with method overrides and denied reads (`tests/test_p1_proxy.py`, against a fake upstream) |
| 3 | `tp target add https://github.com/usememos/memos` → `tp run --target memos` | r-0043, r-0044 | Auth found by probing, first admin created, resource-name IDs and token paging detected. 19 verified, 0 clarify, 0 overrides |
| 4 | `tp target add https://github.com/usememos/memos --no-conventions` | r-0045 | Same fields by exploration alone: 18/19 values equal |
| 5 | `tp watch --target fastapi-template` on the template fork → push → Scan → draft PR | r-0047 (rehearsal); r-0037 (published) | 3 defects reached FIXED_CONFIRMED. **Correction:** the same run raised 3 false P0s, and one "fix" passed only by changing the API contract; retest now refuses that (`contract_diff`). Its report was rebuilt (exec #347) |
