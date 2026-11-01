"""G4–G6 with a scripted model and a scripted runner (no Docker, no API key).

The fake env reads markers in the binding (comments) to decide each case's origin, the way the
real DryRun would report it."""

import ast
import json

import pytest

from tp.core import artifacts, contract, llm, review, runner
from tp.stages.generate import ai, loop

GOOD = 'r = api.get("/api/v1/items/", token=user["token"], params={"skip": data["skip"]})'
TITLE = 'r = api.post("/api/v1/items/", token=user["token"], json={"title": data["title"]})'


class FakeEnv:
    def __init__(self, fx, product: dict | None = None):
        self.fx, self.product, self.calls = fx, product or {}, []

    def ensure(self, variant):
        pass

    def openapi(self, variant):
        return self.fx["openapi"]

    def pytest(self, tests, out, variant="reference", env=None, **kw):
        self.calls.append(
            {"variant": variant, "env": env, "files": sorted(p.name for p in tests.glob("*.py"))}
        )
        records = []
        for f in sorted(tests.glob("*.py")):
            src = f.read_text()
            tree = ast.parse(src)
            case_ids = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                            and n.targets[0].id == "CASE_DATA")  # fmt: skip
            for cid in case_ids:
                origin = None
                if "# BROKEN" in src:
                    origin = "BINDING"
                elif "# WRONGDATA" in src and env["TP_CONFORMANCE"] == "1":
                    origin = "CONFORMANCE"
                elif cid in self.product and "# SHORTER" not in src:
                    origin = self.product[cid]
                records.append({
                    "nodeid": f"{f.name}::test[{cid}]", "case_id": cid,
                    "outcome": "failed" if origin else "passed", "origin": origin,
                    "failure": origin and {"error": f"{origin} failure", "line": 20},
                    "exchanges": [{"setup": False, "method": "GET", "path": "/api/v1/items/",
                                   "query": {}, "json": None, "status": 500, "body": "x"}]})  # fmt: skip
        return {"results": records, "returncode": int(any(r["origin"] for r in records))}


SCRIPT: dict[str, list[dict]] = {}
PROMPTS: list[str] = []


def fake_model(prompt, model, params):
    assert model == ai.MODEL
    PROMPTS.append(prompt)
    uid = json.loads(prompt.split("## The case\n\n")[1].split("\n\n## The routes")[0])["id"]
    step = SCRIPT[uid].pop(0)
    return json.dumps(
        {"binding": step.get("binding", ""), "cannot_bind": step.get("cannot", "")}
    ), {}


@pytest.fixture
def setup(fx, monkeypatch, data):
    SCRIPT.clear()
    PROMPTS.clear()
    monkeypatch.setattr(llm, "anthropic_provider", fake_model)
    (data / "published.json").write_text(json.dumps({"runner": {"digest": "sha256:fake"}}))

    def go(units, script, product=None, options=None, cases=None):
        SCRIPT.update({u: list(s) for u, s in script.items()})
        env = FakeEnv(fx, product)
        contract.load()
        monkeypatch.setitem(contract.SERVICES, "env", env)
        c = artifacts.put("cases", cases or fx["cases"], labels=["pre-generated"])
        s = artifacts.put("spec", fx["spec"], labels=["pre-generated"])
        run_id = runner.start_run("record", options={"units": units, **(options or {})})
        ex = runner.rerun(run_id, "generate", overrides={"cases": c.id, "spec": s.id})
        return run_id, ex, env

    return go


def _out(run_id):
    ex = [e for e in runner.get_run(run_id)["executions"] if e["stage"] == "generate"][-1]
    if not ex["outputs"]:
        return ex, None, None
    return ex, artifacts.get(ex["outputs"]["bound_tests"]).body, artifacts.get(
        ex["outputs"]["ledger"]).body["attempts"]  # fmt: skip


def test_v2_binds_and_leaves_product_failures_to_the_gate(setup):
    run_id, ex, env = setup(["TC-O6-2"], {"TC-O6-2": [{"binding": GOOD}]},
                            product={"TC-O6-2.a": "PRODUCT_ERROR"})  # fmt: skip
    ex, bound, ledger = _out(run_id)
    assert ex["status"] == "succeeded", ex["reason"]
    t = bound["tests"][0]
    assert t["status"] == "BOUND" and t["dry_run"]["TC-O6-2.a"] == "PRODUCT_ERROR"
    assert [a["outcome"] for a in ledger] == ["bound"] and len(PROMPTS) == 1  # no repair call
    assert env.calls[0]["env"]["TP_CONFORMANCE"] == "1"
    assert "EXPECTED = {...}" in PROMPTS[0] and "'422'" not in PROMPTS[0]  # expected hidden


def test_v2_repairs_a_binding_fault(setup):
    run_id, *_ = setup(["TC-O6-2"], {"TC-O6-2": [{"binding": GOOD + "\n# BROKEN"},
                                                 {"binding": GOOD}]})  # fmt: skip
    _, bound, ledger = _out(run_id)
    assert bound["tests"][0]["status"] == "BOUND" and bound["tests"][0]["attempts"] == 2
    assert [a["outcome"] for a in ledger] == ["repair", "bound"]
    assert ledger[0]["classification"] and "-# BROKEN" in ledger[1]["diff"]
    assert PROMPTS[1].startswith(ai.INSTRUCTIONS["v2"])


def test_tampering_is_rejected_and_the_ledger_shows_it(setup):  # G6
    run_id, *_ = setup(["TC-O6-2"], {"TC-O6-2": [
        {"binding": GOOD + "\nwant = EXPECTED[case_id]"},           # reads the oracle
        {"binding": GOOD + "\nexpect.status = print"},               # patches the runtime
        {"binding": GOOD + "\n# WRONGDATA"},                         # sends other inputs
        {"binding": GOOD}]})  # fmt: skip
    _, bound, ledger = _out(run_id)
    assert bound["tests"][0]["status"] == "BOUND"
    assert [a["outcome"] for a in ledger] == ["rejected", "rejected", "repair", "bound"]
    assert any(s.startswith("binding_never_reads_expected") for s in ledger[0]["static"])
    assert any(s.startswith("no_patching") for s in ledger[1]["static"])
    assert set(ledger[2]["origins"].values()) == {"CONFORMANCE"}


def test_unbound_after_max_repairs_goes_to_review(setup):
    broken = [{"binding": f"{GOOD}\n# BROKEN {i}"} for i in range(4)]
    run_id, ex, _ = setup(["TC-O6-2"], {"TC-O6-2": broken})
    assert ex["status"] == "awaiting_review"
    item = next(i for i in review.list_(status="open") if i["kind"] == "unbound")
    assert item["payload"]["unit"] == "TC-O6-2" and "after 3 repairs" in item["payload"]["reason"]
    review.resolve(item["id"], "accept_gap", "po")
    runner.resume(item["id"])
    _, bound, ledger = _out(run_id)
    assert (bound["tests"][0]["status"], bound["tests"][0]["review"]) == ("UNBOUND", "accept_gap")
    assert [a["outcome"] for a in ledger] == ["repair"] * 3 + ["unbound"]


def test_a_repeated_binding_is_a_loop(setup):
    _run_id, *_ = setup(["TC-O6-2"], {"TC-O6-2": [{"binding": GOOD + "\n# BROKEN"}] * 2},
                       options={"max_repairs": "3"})  # fmt: skip
    item = next(i for i in review.list_(status="open") if i["kind"] == "unbound")
    assert item["payload"]["reason"].startswith("loop")


def test_the_writer_may_decline(setup):
    _run_id, _ex, env = setup(["TC-C4-1"], {"TC-C4-1": [{"cannot": "needs control of time"}]})
    item = next(i for i in review.list_(status="open") if i["kind"] == "unbound")
    assert item["payload"]["reason"] == "needs control of time" and env.calls == []


@pytest.mark.parametrize("guards", ["v1", "v2"])
def test_v1_repairs_a_real_defect_away_v2_does_not(setup, guards):  # G5 vs G6
    script = {"TC-C11-1": [{"binding": TITLE}, {"binding": TITLE + "\n# SHORTER"}]}
    run_id, _, env = setup(["TC-C11-1"], script, product={"TC-C11-1.b": "ASSERTION"},
                           options={"guards": guards, "variant": "bug:BUG_TITLE_MAX"})  # fmt: skip
    _, bound, ledger = _out(run_id)
    t = bound["tests"][0]
    assert env.calls[0]["variant"] == "bug:BUG_TITLE_MAX"
    if guards == "v1":  # the defect is "repaired" away: the test now passes on the bug variant
        assert env.calls[0]["env"]["TP_CONFORMANCE"] == "0"
        assert [a["outcome"] for a in ledger] == ["repair", "bound"] and t["dry_run"] == {
            "TC-C11-1.a": None,
            "TC-C11-1.b": None,
            "TC-C11-1.c": None,
        }
    else:  # the failure stands and goes to the gate
        assert [a["outcome"] for a in ledger] == ["bound"]
        assert t["dry_run"]["TC-C11-1.b"] == "ASSERTION"


def test_unsigned_cases_are_refused(setup, fx):
    cases = fx["cases"] | {"signoff": fx["cases"]["signoff"] | {"signed": False}}
    _run_id, ex, _ = setup(["TC-O6-2"], {}, cases=cases)
    assert ex["status"] == "failed" and "not signed" in ex["reason"]
    _run_id, ex, _ = setup(["TC-O6-2"], {}, cases=cases, options={"unattended": True})
    assert "not signed" not in (ex["reason"] or "")  # unattended: goes on, labelled unsigned


def test_status_and_classification_helpers():
    assert loop.classify(["no_try: x"], []) == "generation"
    assert loop.classify([], [{"origin": "BINDING", "error": "KeyError: 'id'"}]) == "locator"
    assert loop.classify([], [{"origin": "BINDING", "error": "make.item() got owner"}]) == "fixture"
    assert loop.classify([], [{"origin": "ASSERTION", "error": ""}]) == "product"
