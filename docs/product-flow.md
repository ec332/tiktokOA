# Test Platform: the end-to-end flow

From a feature request to committed, validated tests and a report, in the web UI.
This guide covers what each screen and option does, which parts an AI agent handles, which parts are fixed code, and which decisions are yours. It also explains why the flow is built this way.

Start it with `uv run tp serve` (API, port 8000) and `cd web && npm run dev` (UI at http://localhost:5173). All times on screen are GMT+8.

---

## The flow at a glance

```
 New run                Run page (one page from start to report)
 ───────                ─────────────────────────────────────────────────────────────────────────────
 repo + branch   ──►  1 Understand ──► 2 Clarify ──► 3 Design + approve ──► 4 Build and check ──► 5 Report
 what to test           (agent)        STOP: you      (agent) STOP: you      (agent + fixed code)    (fixed code)
 rules (optional)                                                                                        │
                                                                                                         ▼
                                                                          confirm defects · try suggested fix
                                                                          · retest my fix  (proof of the fix)
```

The run waits for you only twice: **Clarify** and **Approve**. Everything else runs on its own, and each finished step can be expanded to show exactly what it produced.

---

## Who does what

| Part | Done by | Why |
|---|---|---|
| Reading the story and the code, writing requirements | **Agent** (spec: extractor, investigator) | Reading free text and code is open-ended. Each requirement must cite real code lines, and fixed code checks that the cited lines exist. |
| Writing the clarification questions | **Agent** (spec: question writer) | Phrasing questions is a language task. Which ambiguities get asked is decided by the rules and by fixed checks. |
| Suggested rules for a feature | **Agent** (one cheap call) | Only suggestions. They start unticked, and you choose which apply. |
| Answering questions, confirming code behaviour | **You** | Only the product owner knows what is intended. Answers become part of the requirements. |
| Designing test cases (title, steps, expected result) | **Agent** (cases: drafter) | Choosing what to test is open-ended. Edge values such as 0, 0.01, 100 and 101 come from **fixed calculators**, never from the model. |
| Choosing which tests to generate | **You** (Approve) | The sign-off is the contract. Code is generated only from approved cases. |
| Writing the test code | **Agent** (generate: binding) | Writing code is open-ended. Fixed static checks and a dry run accept or reject it, and the agent repairs on failure within fixed limits. |
| Starting environments, seeding data, running tests | **Fixed code** (env, execute) | Mechanical, and it must be repeatable. Run records are sealed and write-once. |
| Admitting tests (stable? catches mutations?) | **Fixed code** (gate) | A verdict must not depend on a model's opinion. The one exception: the agent proposes whether a surviving mutant is "equivalent", and that only counts once confirmed. |
| Deciding why a test failed (product, test, env, flaky…) | **Fixed code** (triage rules) | Fixed rules over the raw records, so every class is traceable to evidence. |
| Locating the defect (file, line, function) | **Fixed code** (triage localiser) | Traceback, SQL error, the route handler, and the requirement's code references. |
| Suggesting a fix | **Agent** (triage: fix suggestion) | Labelled "AI suggestion". Fixed code checks that the patch applies cleanly and still parses. It is proven only by a retest. |
| Report numbers, lists and wording | **Fixed code** (report) | Every line comes from a record and links to it. No free-form model text, except the labelled fix suggestion. |
| Is it a real defect? | **You** (Confirm / Not a bug in the report) | It doesn't block the run. The report shows each defect as unconfirmed until you decide. |
| Proving a fix | **Fixed code** (retest) | Same test files, same hashes. The test must fail before and pass after on every run. A single passing rerun is never treated as proof. |

The agent is Claude Haiku 5.5 by default (the smallest model that passed each stage's measured check). AI cost is shown on the run page; a full run is under $0.10.

---

## Screen 1: New run

### Repository
| Option | Meaning |
|---|---|
| **Path to a local git repo** | Your checkout, for example `~/Desktop/full-stack-fastapi-template`. The platform **clones** it into `data/sut/` and never changes your checkout. If you have uncommitted changes you get a warning, because the clone only has commits. |
| **Branch to test** | The code under test, for example `feature/item-pricing`. Its latest commit is cloned, built and started. |
| **Compared with (base)** | Usually `master`. The diff between base and branch is "the feature": its changed lines are where the platform plants mutations to measure test strength. |
| **Stack status** | "starting the stack" right after you pick a branch: the platform builds and starts it while you type. A new commit takes a few minutes. |

### What to test (the three modes)
| Mode | You give | What the platform does |
|---|---|---|
| **Feature** | A few sentences ("Items get a price and a discount percentage…") | Turns them into requirements, reads the code for the rest, and asks you about gaps and suspicious behaviour. The usual choice. |
| **User story** | A full story with acceptance criteria | Each criterion becomes a requirement. Questions are asked only where a criterion is unclear. |
| **Endpoint** | Only the API endpoints, no text | Requirements come from what the code does. You confirm each behaviour ("Yes, intended / No, that's wrong / Not part of this feature"). |

**Routes to focus on.** Optional in Feature and User story mode, required in Endpoint mode. The platform reads only these routes, the app modules they import, and the files your branch changed. Fewer files means lower cost and less drift.

### Rules for this run (optional)
Rules apply to **this run only** and are **added after** the defaults. The defaults always apply and can't be switched off.

| Section | Default | What you can add |
|---|---|---|
| **Clarifications** | 6 standing rules, e.g. "Every input with a size, length or count: check the minimum, the maximum, and just outside both." | Your own lines, and **Suggest rules for this feature**: the agent proposes general rules ("Money fields: ask how amounts are rounded…"). They start unticked, and they never state facts about your code, so they can't give away the bugs. |
| **Test cases** | The drafter's own rules | Your own lines, e.g. "Every price test includes 0 and 0.01", plus switches for the four test-design templates: boundary values, decision table (including authorisation), error and exception paths, error guessing. |
| **Gate** | Stability runs 5 · min. mutants killed 1 · gap distance 3 lines · every requirement needs a test | Numbers within fixed limits. The gate is fixed code, so its rules are settings rather than text. |

**Start run** opens the run page.

---

## Screen 2: the run page

Five steps. A running step shows one line of progress and the elapsed time. A step that needs you becomes a card with **NEEDS YOU**, and the browser tab title changes to "Needs you · r-00NN". Click a finished step's name (▸) to expand what it produced.

### 1. Understand the feature (agent)
Reads your text and the focused code, writes requirements (`C-n` from your text, `O-n` from observed code behaviour), and notes suspicious code as findings.
**Expanded:** every requirement with Given / When / Then, the expected status, and whether it is confirmed, assumed or out of scope.

### 2. Clarify (you)
Two kinds of item:
- **Questions** about gaps. Each has a recommended answer, which is preselected. Your options:
  - **Other…**: type the intended behaviour, e.g. "0 to 100 inclusive is accepted; anything else returns 422".
  - **Let the platform assume**: the answer is recorded as an assumption, and tests built on it are reported as "requirement only assumed", not as defects.
- **"The code does this; is it intended?"**:
  - **Yes, intended**: the requirement is confirmed.
  - **No, that's wrong**: the behaviour is marked as a suspected defect, and a follow-up question asks what should happen.
  - **Not part of this feature**: the requirement is dropped.

There can be a second round of follow-ups. **Expanded:** every question and its answer, with who answered.

### 3. Design and approve tests (agent, then you)
The agent designs test cases from the requirements, the templates and your rules. A requirement that has no normal-case ("acceptance") test gets one automatic re-draft.
**Approve** shows each test as a title plus "catches …" (the bug it is meant to find), grouped by requirement. **Untick** to leave a test out. It is not generated, and nothing is rewritten. Notes from the design check, such as a requirement still lacking a case, appear at the top. **Generate N tests** signs the cases. Only signed cases become code.
**Expanded:** the approved tests, each with a link to its results.

### 4. Build and check (agent + fixed code)
1. **Test runner:** rebuilt automatically if your branch changed ("Rebuilding the test runner for your branch").
2. **Generate (agent):** writes the code for each approved case. Fixed static checks and a dry run accept it, and the agent repairs failures within a fixed number of rounds. A case that can't be bound is listed as a gap, never silently dropped.
3. **Gate (fixed code):**
   - **Stability:** each test runs K times on your branch, plus once in shuffled order. Tests whose results disagree are FLAKY.
   - **Routing:** a stable test that *fails* on your branch is a "likely defect" when its requirement is confirmed, or a "spec gap" when the requirement is only assumed.
   - **Strength:** the gate plants small code changes (mutants) in the branch's changed lines and in the route handlers. A test that catches none of them is WEAK.
4. **Execute (fixed code):** runs every generated test over the network against the real stack, 3 runs each, so each one's gate status and its real-stack result sit side by side. Raw records (every request, response, assertion and backend log line) are sealed.

**Expanded:** every test with its gate status, stability, and result on your branch ("consistent fail · 3 of 3 runs failed").

### 5. Report (fixed code, with labelled AI suggestions)
| Section | Meaning |
|---|---|
| **Summary strip** | Defects found (one per cause), problematic tests, areas to improve, tests admitted, tests approved at sign-off. |
| **Defects** | One card per **cause**; several requirements failing at the same line show as one card. Each card has the failing request and response, the failed assertion, **Where** (file:line, from the localiser), **Found by** (the tests), the **Suggested change** (AI, with "applies cleanly" or the reason it can't), and **Evidence** (the raw records). |
| **Problematic tests** | Tests that failed or are untrustworthy, each with a fixed reason: product defect, requirement only assumed, flaky, weak, the test itself is wrong, environment error. Repeated boundary points are counted ("×2"). |
| **Areas to improve** | No test for a requirement · **Unnoticed change**: a planted code change no test caught, with its diff (where to add or sharpen a test) · assumed answers · requirements still open. |
| **Rules used in this run** | The routes, rules, templates and gate settings the run actually used. |

**Every reference is a link.** A requirement id opens step 1 at that requirement. A test in "Found by" opens step 3 at that test. A test in "Problematic tests" opens step 4 at its result. The item is highlighted.

### Defect actions
| Button | What happens |
|---|---|
| **Confirm defect** / **Not a bug** | Your decision, recorded in the decision log. It doesn't re-run anything. |
| **Try suggested fix** | Builds your branch **plus the AI patch** as a separate image and reruns **the same test files** (same hashes). The verdict is **FIXED_CONFIRMED** only if the tests that failed before now pass on every run *and* the previously passing tests still pass. Your branch is not changed. |
| **Retest my fix** | After you commit a fix to the branch yourself: the platform fetches the new commit and reruns the same tests, with the same proof rule. |

---

## Design decisions and why

| Decision | Choice | Why |
|---|---|---|
| Who writes the feature | You (the platform tests code, it does not write it) | A model writing both the code and its tests would share its own blind spots. |
| Your repo | Cloned, never patched in place | The platform's hooks (test-info endpoint, trace headers) go into its own copy. |
| Human stops | Two: Clarify and Approve | Questions decide what "correct" means, and the sign-off decides what gets built. Everything else has a logged default; defect confirmation is in the report and doesn't block. |
| Rules | Defaults always on, plus yours for this run only | Runs stay comparable, and the report lists exactly what was added. |
| Suggested rules | General, unticked, no code facts | A suggestion that stated what the code does would leak the bugs. |
| AI vs fixed code | AI proposes, fixed code decides | Requirements, cases, test code and fix suggestions are AI. Admission, verdicts, classification, localisation, numbers and retest proof are fixed code. |
| Expected values | Calculators, not the model | Edge points (99, 100, 101), page sizes and discounted prices (`percent_off`: Decimal, half-up by default) must be exact. The model only picks a calculator and its inputs. |
| Test strength | Mutation on the branch's changed lines (field constraints included) and route handlers | Passing tests prove little. A test should catch a deliberate change in the feature it covers. |
| Flaky and environment failures | K runs, shuffled order, environment probes, separate classes | A failure that isn't stable, or that came from the environment, is never reported as a product defect. |
| Proof of a fix | Same test hashes; fail before and pass after on every run; no regressions | A rerun that happens to pass is not proof. |
| Raw records | Sealed, write-once, linked from the report | Every claim can be traced to the request and response that support it. |

---

## How this maps to the assignment

| Requirement | Where it is met |
|---|---|
| 1. AI in requirement understanding and case generation; which parts are deterministic or need review | Steps 1, 3 and 4 above, and the "Who does what" table. |
| 2. Generation → data and environment → execution → judgement → report; normal and edge cases | Steps 1–5. Every requirement gets an acceptance (normal) case, and the templates add boundary and error cases. |
| 3. A real or seeded defect, localisation evidence, retest after the fix | Report → Defects (Where + Evidence) → Try suggested fix / Retest my fix → FIXED_CONFIRMED. |
| 4. Wrong tests, environment failures, flaky tests, raw records, no "rerun = fixed" | Gate (test is wrong, flaky), environment probes (ENV class), sealed records, the retest proof rule. |

## Known limits

- One repo (this FastAPI template) and one run at a time. Other repos and parallel runs are parked.
- Test quality depends on your answers. If you choose "Let the platform assume", failures are reported as spec gaps, not defects.
- "Cancel run" stops after the current step finishes, not mid-step.
