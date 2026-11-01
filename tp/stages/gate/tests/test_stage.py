"""The gate stage end to end with a scripted env and model (no Docker, no API key)."""

import json

import pytest

from tp.core import artifacts, contract, llm, review, runner
from tp.stages.gate import ai

UNITS = ["TC-O6-2", "TC-C11-1", "TC-C9-2", "TC-C8-3"]
M1, M2 = "app.crud.x_f__mutmut_1", "app.crud.x_f__mutmut_2"
CRUD_ORIG = "def f(x):\n    return x + 1\n"
CRUD_MUT = ("def f(x):\n    return x + 1\n\n\ndef x_f__mutmut_orig(x):\n    return x + 1\n\n\n"
            "def x_f__mutmut_1(x):\n    return x - 1\n\n\ndef x_f__mutmut_2(x):\n    return x + 2\n")  # fmt: skip


class FakeEnv:
    def __init__(self, tmp, nodes):
        self.tmp, self.nodes, self.calls = tmp, nodes, []
        (tmp / "src/backend/app").mkdir(parents=True)
        (tmp / "src/backend/app/crud.py").write_text(CRUD_ORIG)

    def ensure(self, variant):
        pass

    def down(self, variant):
        self.calls.append(("down", variant))

    def code_scope(self):
        return {"mutate": ["backend/app/crud.py"], "src": self.tmp / "src"}

    def runner(self, cmd, writable=None, **kw):
        out = writable["/work/out"]
        for d, text in (("mut", CRUD_MUT), ("orig", CRUD_ORIG)):
            (out / d / "app").mkdir(parents=True, exist_ok=True)
            (out / d / "app/crud.py").write_text(text)
        return ""

    def _outcome(self, node, variant, env):
        mutant = env.get("MUTANT_UNDER_TEST")
        if variant == "reference" and node.endswith("[TC-O6-2.a]") and not mutant:
            return "failed", "PRODUCT_ERROR"
        if variant == "reference" and "TC-C9-2" in node and env.get("_run") == 2:
            return "failed", "BINDING"  # one bad run out of six: flaky
        if variant == "bug:BUG_TITLE_MAX" and node.endswith("[TC-C11-1.b]"):
            return "failed", "ASSERTION"
        if mutant == M1 and node.endswith("[TC-C11-1.a]"):
            return "failed", "ASSERTION"
        return "passed", None

    def pytest(self, tests, out, variant="reference", mode="in_process", env=None, args=(),
               mounts=None, matrix=None):  # fmt: skip
        env = dict(env or {})
        self.calls.append(("pytest", variant, mode, env.get("MUTANT_UNDER_TEST"), bool(matrix)))
        env["_run"] = sum(1 for c in self.calls if c[:3] == ("pytest", "reference", "in_process")
                          and not c[3] and not c[4]) - 1  # fmt: skip
        wanted = [a.removeprefix("/work/tests/") for a in args] or self.nodes

        def recs(nodes, e):
            out = []
            for n in nodes:
                o, origin = self._outcome(n, variant, e)
                out.append({"nodeid": n, "outcome": o, "origin": origin,
                            "case_id": n.split("[")[1].rstrip("]"),
                            "reached": ["app.crud.x_f"] if e.get("MUTANT_UNDER_TEST") == "stats"
                            else []})  # fmt: skip
            return out

        if matrix:
            return {"matrix": {j["key"]: recs([a.removeprefix("/work/tests/") for a in j["args"]],
                                               j["env"]) for j in matrix}}  # fmt: skip
        return {"results": recs(wanted, env)}


def fake_model(prompt, model, params):
    assert model == ai.MODEL
    survivors = json.loads(prompt.split("<survivors>\n")[1].split("\n</survivors>")[0])
    return json.dumps(
        {
            "proposals": [
                {
                    "mutant": s["mutant"],
                    "equivalent": s["mutant"] == M2,
                    "reason": "x + 2 is never observed",
                }
                for s in survivors
            ]
        }
    ), {}


@pytest.fixture
def gate(fx, monkeypatch, data, tmp_path):
    monkeypatch.setattr(llm, "anthropic_provider", fake_model)
    data.mkdir(parents=True, exist_ok=True)
    (data / "published.json").write_text(json.dumps({"runner": {"digest": "sha256:fake"}}))
    bt = fx["bound_tests"] | {
        "tests": [t for t in fx["bound_tests"]["tests"] if t["unit"] in UNITS]
    }
    nodes = [f"{t['file']}::{t['file'][:-3]}[{c}]" for t in bt["tests"] for c in t["case_ids"]]
    env = FakeEnv(tmp_path, nodes)
    contract.load()
    monkeypatch.setitem(contract.SERVICES, "env", env)

    def go(options=None):
        ids = {t: artifacts.put(t, b, labels=["pre-generated"]).id
               for t, b in (("bound_tests", bt), ("cases", fx["cases"]), ("spec", fx["spec"]))}  # fmt: skip
        run_id = runner.start_run("record", options=options or {})
        ex = runner.rerun(run_id, "gate", overrides=ids)
        return run_id, ex

    return go, env


def _out(run_id):
    ex = [e for e in runner.get_run(run_id)["executions"] if e["stage"] == "gate"][-1]
    return ex, *(artifacts.get(ex["outputs"][t]).body for t in ("gate_report", "kill_matrix"))


def test_gate_routes_measures_and_asks_about_equivalence(gate):
    go, env = gate
    run_id, ex = go()
    assert ex["status"] == "awaiting_review", ex["reason"]
    item = next(i for i in review.list_(status="open") if i["kind"] == "equivalent_mutant")
    assert item["payload"]["mutant"] == M2 and "x + 2" in item["payload"]["diff"]
    review.resolve(item["id"], "equivalent", "po")
    runner.resume(item["id"])
    ex, report, km = _out(run_id)
    status = {r["case_id"]: r["status"] for r in report["rows"]}
    assert status["TC-O6-2.a"] == "CANDIDATE_DEFECT"  # O-6 confirmed + signed + PRODUCT_ERROR
    assert status["TC-C11-1.a"] == "ADMITTED"  # the only killer of M1
    assert status["TC-C11-1.b"] == "ADMITTED"  # killed its seed in-process and over the network
    assert status["TC-C9-2"] == "FLAKY"
    assert status["TC-C8-3"] == "WEAK"
    row = next(r for r in report["rows"] if r["case_id"] == "TC-C11-1.b")
    assert row["seeded"] == {"flag": "BUG_TITLE_MAX", "in_process": "ASSERTION",
                             "network": "ASSERTION", "killed": True}  # fmt: skip
    assert next(r for r in report["rows"] if r["case_id"] == "TC-C11-1.c")["misses_target"]
    assert report["mutants"] == {"total": 2, "killed": 1, "equivalent": 1, "not_covered": 0,
                                 "strength": 1.0}  # fmt: skip
    assert report["criteria"]["C-11"]["strength"] == 1.0
    assert [s["mutant"] for s in report["survivors"]] == [M2] and report["survivors"][0][
        "equivalent"
    ]
    assert km["cells"] == {"test_tc_c11_1.py::test_tc_c11_1[TC-C11-1.a]": {M1: "K"}}
    assert "TC-C11-1.a]," in km["csv"] and ("down", "bug:BUG_TITLE_MAX") in env.calls
    assert report["cache"] == "miss"


def test_second_run_with_no_changes_is_served_from_the_cache(gate):  # K7
    go, env = gate
    go()
    for i in review.list_(status="open"):
        review.resolve(i["id"], "not_equivalent", "po")
        runner.resume(i["id"])
    before = len(env.calls)
    run_id, _ = go()
    for i in review.list_(status="open"):
        review.resolve(i["id"], "not_equivalent", "po")
        runner.resume(i["id"])
    _, report, _ = _out(run_id)
    assert report["cache"] == "hit" and len(env.calls) == before  # no test was run again
    assert report["mutants"]["strength"] == 0.5  # nothing confirmed equivalent this time


def test_v1_admits_whatever_passes_once(gate):  # K2
    go, _ = gate
    run_id, _ = go({"admit": "v1"})
    _, report, km = _out(run_id)
    status = {r["case_id"]: r["status"] for r in report["rows"]}
    assert status["TC-O6-2.a"] == "REJECTED"  # the real defect is thrown away
    assert status["TC-C8-3"] == "ADMITTED" and km["mutants"] == []  # weak test let through
