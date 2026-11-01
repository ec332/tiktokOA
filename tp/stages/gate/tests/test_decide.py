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
