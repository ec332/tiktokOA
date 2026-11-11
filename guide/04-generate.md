# Stage 4: Generate (technical reference)

[← Guide](../GUIDE.md) · previous: [Cases + Sign-off](03-cases.md) · next: [Gate](05-gate.md)

| | |
|---|---|
| Module | `tp/stages/generate/stage.py` (`GenerateStage`), `scaffold.py`, `checks.py`, `loop.py`, `ai.py` |
| Version | `1.0.0+p<prompt hash>` |
| Consumes | `cases`, `spec`, `run_options` |
| Produces | `bound_tests`, `ledger` |
| Model | writes the **BINDING** region of each test only, and repairs it |
| Person | `unbound` (`rewrite_case` / `accept_gap`) |
| Events | `runner_rebuilding`, `runner_rebuilt`, `bindings_written`, `dry_run`, `tests_bound` |

## Purpose

This stage turns each signed case unit into a runnable pytest file. The model may write only the few lines that make the request (the binding). The case data, the expected values and the assertions are fixed code, locked by a hash.

## Preconditions

- The cases must be signed: `cases are not signed: generate consumes signed cases only`. Unattended runs are the exception and carry the `unsigned` label.
- `guards` must be `v1` or `v2`.
- The reference stack must be up (`reach.ensure("reference")`).
- `ensure_runner` checks the runner image:
  - **White-box**: the runner image holds a copy of the SUT code, so it is rebuilt whenever the backend image changes (`runner_rebuilding`).
  - **Black-box**: the generic `images/runner-blackbox` runner, tagged by the hash of its wheel.

## The scaffold

`scaffold.py` writes one file per unit. Only the region between the `BINDING` markers is open to the model:

```python
UNIT = 'TC-O3-1'
CASE_DATA = {'TC-O3-1': {'discount_percent': 100, 'price': '10.00'}}
HINTS = {'TC-O3-1': {}}
REQUEST = {'TC-O3-1': {'auth': True, 'routes': ['POST /api/v1/items/']}}
EXPECTED = {'TC-O3-1': {'observe': {'percent_off': '0.00'}}}

@pytest.mark.tp_case(unit=UNIT)
@pytest.mark.parametrize("case_id", list(CASE_DATA))
def test_tc_o3_1(make, case_id):
    data, hints = CASE_DATA[case_id], HINTS[case_id]
    user = make.user()
    # >>> BINDING
    r = api.post("/api/v1/items/", token=user["token"], json={...})
    observe("percent_off", r.json()["final_price"])
    # <<< BINDING
    # >>> LOCKED sha256=ce2994ea…
    api.under_test(CASE_DATA[case_id], REQUEST[case_id])
    expect.equals('percent_off', EXPECTED[case_id]["observe"]['percent_off'], id=f"{case_id}/percent_off")
    # <<< LOCKED
```

`lock_hash` is the sha256 of the locked block. The retest later uses it to show the assertions didn't change.

## The binding loop (`loop.bind_all`)

1. **Write.** `ai` is given the unit, its criterion, the OpenAPI operations and the target's factory docs. It returns either `{binding}` or `{cannot_bind: reason}`. A refusal is recorded as `declined`, and the unit is `UNBOUND` with that reason.
2. **Static checks** (`checks.py`). These are AST rules; any problem rejects the attempt (classified `generation`):

   | Rule | Refuses |
   |---|---|
   | Imports | Anything except `pytest` and `platform_rt.{ABSENT, api, expect, observe, eventually}` |
   | Nondeterminism | The names `random`, `time`, `datetime`, `uuid`, `secrets`, `os`, `sys`; attributes such as `sleep`, `now`, `uuid4`, `randint`, `shuffle` and `perf_counter` |
   | Dynamic code | `eval`, `exec`, `compile`, `__import__`, `open`, `getattr`/`setattr`/`delattr`, `globals`, `locals`, `vars`, `breakpoint`, `input` |
   | Protected names | Reassigning `api`, `expect`, `observe`, `CASE_DATA`, `EXPECTED`, `HINTS`, `REQUEST`, `UNIT`, `make`, … or mutating them (`update`, `pop`, `__setitem__`, …) |
   | Skips | `skip`, `xfail`, `importorskip`, `skipif`, `fail`, `exit` |
   | Lock | Any change to the LOCKED block or the header |

3. **Dry run.** The test runs once in the runner against the reference variant. Each failure carries an **origin** (`platform_rt/origin.py`).
4. **Classify and repair.** Which origins the binding may repair depends on `guards`:
   - **`v2`** (default): only `BINDING` and `CONFORMANCE`. A failure in the product (`ASSERTION`, `PRODUCT_ERROR`, `CONTRACT`) is left alone, because it may be a real defect.
   - **`v1`**: also `ASSERTION`, `PRODUCT_ERROR`, `CONTRACT` and `DATA`.
   - **Never repaired**: `ENV`, `PLATFORM` and `TIMEOUT` (the test isn't at fault). These units get status `ERROR`.

   The ledger also classifies each failed attempt as `generation`, `locator` (404/405, path or `KeyError`), `fixture` (factories) or `product`/`environment`. The model gets the failure text and tries again, up to `MAX_REPAIRS = 3` repairs after the first write.
5. **Outcome per unit:**
   - `BOUND`: the dry run passed, or failed only for reasons it may not repair. A product failure on the reference variant is kept: it's the gate's job to judge it.
   - `UNBOUND`: declined, or still failing after the repairs.
   - `ERROR`: the environment or platform failed.

## Extra entries (`ENTRY_PROVIDERS`)

Fixed code adds these after the case units:

| Provider | Adds |
|---|---|
| `_families` | One file holding the **generic check families** (`platform_rt/families.py`). Each node is its own criterion, `G:<family>:<METHOD> <path>`. The families are `properties`, `auth_required`, `unknown_id`, `invalid_input`, `read_back`, `delete_final` and `paging`, plus `dependency_called` and `dependency_failure_handled` when a stand-in is wired. Up to `per_family` (5) operations per family, the run's routes first |
| `ui.entries` | One `.ui.yaml` per verified browser flow (`kind: ui`), run by `images/runner-browser` |
| `suite.entries` | Imported community suites (`kind: suite`, labelled pre-generated). None are in use |

The runner image is picked per run: the browser runner if any UI entry exists, otherwise black-box or white-box.

## Review: `unbound`

Each `UNBOUND` unit becomes a review item holding its reason, its first 3 failures and its last source.
- `accept_gap` lists the unit as a gap; the report shows it under P1 as "signed case not bound to a test".
- `rewrite_case` sends it back to be redesigned.

## Output: `bound_tests`

```
{cases, signature (the sign-off hash), guards, variant, runner{digest…}, openapi_hash,
 tests[{unit, ac, file, source, lock_hash, case_ids[], case_data, routes[], status: BOUND|UNBOUND|ERROR,
        attempts, failures[], reason?, review?, kind?: ui|suite, generic?}],
 summary{BOUND: n, …}}
```

`ledger.attempts[]` holds one row per attempt: `{unit, attempt, kind: write|repair, outcome, classification, origins}`.

The artifact id of `bound_tests` is the identity of the test set. The gate, execute, the retest and domain-pack comparisons all refer to it.

## Files and tests

- `scaffold.py`, `checks.py`, `loop.py`, `ai.py`.
- `image.py`: the runner images.
- `ui.py`, `suite.py`.
- `stage.py`: `families_entry`.
- `platform_rt/`: the runtime library inside the runner.
- Tests: `test_scaffold_checks.py`, `test_loop.py`, `test_entries.py`, `test_docker.py`.
