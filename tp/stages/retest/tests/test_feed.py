"""IterationFeeder (V4): which worklist items become case candidates, in what order, capped."""

from tp.stages.retest import feed


def surv(ac, line, cls="test_gap", eq=False):
    return {"class": cls, "criteria": [ac], "file": "items.py", "line": line,
            "diff": "-a == b\n+a != b", "equivalent": eq}  # fmt: skip


def test_candidates_order_filter_and_cap():
    report = {"worklist": {"P1": [
        {"ac": "C-3", "what": "weak test (cannot fail on any mutant)", "closes_it": "a row"},
        {"ac": "C-4", "what": feed.NOT_A_CASE_GAP, "closes_it": "bind TC-C4-1"}]}}  # fmt: skip
    gate = {"survivors": [surv("O-8", 56), surv("O-8", 57), surv("O-10", 54),
                          surv("O-9", 1, cls="out_of_scope"), surv("O-11", 3, eq=True)]}  # fmt: skip
    cases = {"deferred": [{"ac": "C-9", "id": "TC-C9-9", "deferred": "over the extras budget"}]}
    out = feed.candidates(gate, report, cases)
    assert [c["criterion"] for c in out] == ["C-3", "O-8", "O-10", "C-9"]  # C-4 is a binding gap
    assert out[1]["basis"][0] == "surviving mutant at items.py:56: -a == b +a != b"
    many = {"survivors": [surv(f"X-{i}", i) for i in range(20)]}
    assert len(feed.candidates(many, {"worklist": {}}, {})) == feed.CAP
