"""Every gate rule on hand-made records (no Docker)."""

import pytest

from tp.stages.gate import decide


def rec(node, outcome="passed", origin=None, case_id="TC-1"):
    return {"nodeid": node, "outcome": outcome, "origin": origin, "case_id": case_id}


def test_stability_flags_disagreement_and_missing_runs():
    runs = [[rec("a"), rec("b")], [rec("a"), rec("b", "failed", "ASSERTION")], [rec("a")]]
    s = decide.stability(runs)
    assert s["a"]["stable"] and s["a"]["outcome"] == "passed"
    assert not s["b"]["stable"]  # disagreed, and missing from one run


@pytest.mark.parametrize(("node", "crit", "signed", "want"), [
    ({"stable": False}, {}, True, "FLAKY"),
    ({"stable": True, "outcome": "passed"}, {}, True, None),
    ({"stable": True, "outcome": "failed", "origin": "PRODUCT_ERROR"}, {"status": "confirmed"}, True, "CANDIDATE_DEFECT"),
    ({"stable": True, "outcome": "failed", "origin": "ASSERTION"}, {"status": "confirmed"}, False, "SPEC_GAP"),
    ({"stable": True, "outcome": "failed", "origin": "ASSERTION"}, {"status": "assumed"}, True, "SPEC_GAP"),
    ({"stable": True, "outcome": "failed", "origin": "ASSERTION"},
     {"status": "confirmed", "human_judgement": True}, True, "SPEC_GAP"),
    ({"stable": True, "outcome": "failed", "origin": "CONFORMANCE"}, {"status": "confirmed"}, True, "REJECTED_ORACLE"),
    ({"stable": True, "outcome": "failed", "origin": "ENV"}, {"status": "confirmed"}, True, "ERROR"),
])  # fmt: skip
def test_reference_router(node, crit, signed, want):
    assert decide.route(node, crit, signed) == want


def test_kills_count_only_failures_for_the_claimed_reason():
    cells = decide.kills({"m1": [rec("a", "failed", "ASSERTION"), rec("b", "failed", "DATA"),
                                 rec("c")], "m2": [rec("a", "failed", "ENV")]})  # fmt: skip
    assert cells == {"a": {"m1": "K"}, "b": {"m1": "S"}}


def test_admission_unique_redundant_weak_unmeasured_seeded():
    unit_of = {"u1.a": "U1", "u1.b": "U1", "u2": "U2", "u3": "U3"}
    cells = {"u1.a": {"m1": "K", "m2": "K"}, "u1.b": {"m1": "K"}, "u2": {"m2": "K"}}
    assert decide.admit("u1.a", unit_of, cells, False, True) == ("ADMITTED", 2, 1)
    assert decide.admit("u1.b", unit_of, cells, False, True) == ("ADMITTED", 1, 1)  # same unit
    assert decide.admit("u2", unit_of, cells, False, True) == ("ADMITTED_REDUNDANT", 1, 0)
    assert decide.admit("u3", unit_of, cells, False, True) == ("WEAK", 0, 0)
    assert decide.admit("u3", unit_of, cells, True, True)[0] == "ADMITTED"  # killed its seed
    assert decide.admit("u3", unit_of, {}, False, False)[0] == "ADMITTED_UNMEASURED"


def test_survivor_classes_and_strength():
    ms = [
        {"name": "k", "file": "f.py", "line": 10, "diff": ""},
        {"name": "gap", "file": "f.py", "line": 12, "diff": ""},
        {"name": "far", "file": "f.py", "line": 40, "diff": ""},
        {"name": "nl", "file": "f.py", "line": None, "diff": ""},
    ]
    s = decide.survivors(ms, {"t": {"k": "K"}}, {"gap": ["t"]}, [("f.py", 14, "C-1")])
    assert [(x["mutant"], x["class"], x["criteria"], x["covered"]) for x in s] == [
        ("gap", "test_gap", ["C-1"], True), ("far", "out_of_scope", [], False),
        ("nl", "unclassified", [], False)]  # fmt: skip
    assert decide.strength(3, 10, 2) == 0.375 and decide.strength(0, 1, 1) is None
    assert decide.matrix_csv(["t"], ["k", "gap"], {"t": {"k": "K"}}) == "test,k,gap\r\nt,K,.\r\n"


def test_run_settings_are_clamped_and_default_to_the_built_in_values():
    """M8 gate D22."""
    assert decide.settings({}) == decide.DEFAULTS
    s = decide.settings({"gate": {"k": 50, "min_kills": 2, "near": -1, "every_criterion": False,
                                  "bogus": 1}})  # fmt: skip
    assert s == {"k": 10, "min_kills": 2, "near": 0, "every_criterion": False}


def test_min_kills_turns_a_single_kill_weak():
    cells = {"a": {"m1": "K"}}
    assert decide.admit("a", {"a": "u1"}, cells, False, True)[0] == "ADMITTED"
    assert decide.admit("a", {"a": "u1"}, cells, False, True, min_kills=2)[0] == "WEAK"
    assert decide.admit("a", {"a": "u1"}, cells, True, True, min_kills=2)[0] == "ADMITTED"


def test_uncovered_lists_criteria_without_an_admitted_test():
    rows = [{"ac": "C-1", "status": "ADMITTED_REDUNDANT"}, {"ac": "C-2", "status": "WEAK"}]
    assert decide.uncovered(["C-1", "C-2", "C-3"], rows) == ["C-2", "C-3"]
    assert decide.uncovered(["C-2"], [{"ac": "C-2", "status": "CANDIDATE_DEFECT"}]) == []


def test_gate_without_mutation_reports_not_measured_and_eventual_consistency(tmp_path):
    """blackbox D16: stability only; retries in a stable pass mark it eventually consistent."""
    from types import SimpleNamespace

    from tp.stages.gate.stage import GateStage

    def run(tests, out, variant, env=None):
        rec = {"nodeid": "t.py::test_a", "case_id": "TC-1", "outcome": "passed", "origin": None,
               "retries": {"attempts": 2 if env else 0, "waited_s": 0.3}}  # fmt: skip
        return {"results": [rec, {"nodeid": "t.py::test_b", "case_id": "TC-2",
                                  "outcome": "passed", "origin": None}]}  # fmt: skip

    ctx = SimpleNamespace(emit=lambda *a, **k: None, strategies=SimpleNamespace(
        checks=SimpleNamespace(run=run), mutation=SimpleNamespace(available=lambda: False)))  # fmt: skip
    info = {"bound_tests": "sha256:abcdef0123", "settings": {"k": 2}}
    m = GateStage()._measure(ctx, tmp_path, [], info)
    assert m["strength"] == "not_measured" and m["mutants"] == []
    assert m["eventually"] == ["t.py::test_a"]
    assert all(s["stable"] for s in m["stability"].values())
