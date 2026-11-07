"""Report from a frozen artifact set (real execute run x252 + triage #274): golden report.json,
every number has a composition (R1), the worklist (R2), sections populated (R3), HTML (R4)."""

import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tp.stages.report import build, render
from tp.stages.report.stage import ReportStage

FX = Path(__file__).parents[1] / "fixtures"
EXPECTED = Path(__file__).parents[1] / "expected" / "report.json"
NAMES = ("spec", "cases", "gate_report", "run_records", "triage")
TEMPLATE_STRATEGIES = {"reach": "container", "spec": "served", "auth": "override_module",
                       "data": "override_module", "state": "transaction", "mutation": "in_process",
                       "safety": "unrestricted", "checks": "pytest_cases"}  # fmt: skip


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))


@pytest.fixture(scope="module")
def report():
    b = {n: json.loads((FX / f"{n}.json").read_text()) for n in NAMES}
    return build.build(
        b["spec"],
        b["cases"],
        b["gate_report"],
        b["run_records"],
        b["triage"],
        [],
        ["real"],
        strategies=TEMPLATE_STRATEGIES,
    )


def test_golden_report_json(report):
    if os.environ.get("TP_UPDATE_GOLDEN"):
        EXPECTED.write_text(json.dumps(report, indent=1, sort_keys=True))
    assert json.loads(EXPECTED.read_text()) == json.loads(json.dumps(report))


def test_every_number_has_a_composition(report):
    metrics = {k: v for k, v in report.items() if k not in ("evidence", "defects", "traceability")}
    assert list(build.walk(metrics)) == []
    assert list(build.walk({"x": {"y": 3}})) == [".x.y"]  # the check itself catches a bare count


def test_worklist_orders_real_defects_first_with_evidence(report):
    p0 = report["worklist"]["P0"]
    assert [d["ac"] for d in p0 if d["kind"] == "real"] == ["O-5", "O-6", "O-7"]
    assert p0[0]["kind"] == "real" and p0[-1]["kind"] == "seeded"
    o6 = next(d for d in p0 if d["ac"] == "O-6")
    assert o6["suspects"][0]["upstream_line"] == 15 and o6["cases"]["n"] == 5
    assert all(e in report["evidence"] for d in p0 for e in d["evidence"])
    assert {p["what"] for p in report["worklist"]["P1"]} >= {
        "weak test (cannot fail on any mutant)",
        "signed case not bound to a test",
    }
    assert any(p["case"] == "TC-C13-1.a" for p in report["worklist"]["P3"])


def test_scorecard_from_the_real_run(report):
    sc = report["scorecard"]
    assert (sc["seeded_authored"]["n"], sc["seeded_authored"]["d"]) == (2, 4)
    assert (sc["real_defects"]["n"], sc["real_defects"]["d"]) == (3, 3)
    assert (sc["triage_correct_seeded"]["n"], sc["triage_correct_seeded"]["d"]) == (4, 4)
    assert (sc["localisation"]["top1"]["n"], sc["localisation"]["top1"]["d"]) == (2, 3)
    assert sc["seeded_blind"] == "not run (M7)" and sc["fail_before_pass_after"] == "not run (M5)"
    assert (sc["mutation_strength"]["n"], sc["mutation_strength"]["d"]) == (15, 134)


def test_sections_populated(report):
    assert len(report["traceability"]) == 59
    cov = report["coverage"]
    assert cov["normal"]["n"] + cov["boundary"]["n"] + cov["exception"]["n"] == 59
    assert (
        report["uncovered"]["unbound_cases"]
        and report["uncovered"]["behaviour_with_no_requirement"]
    )
    assert report["ai_decisions"]["gate_equivalence"]["equivalent"]["d"] == 119


def test_html_is_self_contained_and_embeds_cited_evidence(report):
    page = render.html(report)
    assert "<script" not in page and "http://" not in page.split("<main>")[0]
    assert 'id="log:x252.reference.a1/TC-O5-1/7"' in page and "NotNullViolation" in page
    assert "P0 defects: 6" in render.cli(report)
    assert "## P0 — defects" in render.summary_md(report)


def test_stage_writes_the_files(tmp_path):
    arts = {n: NS(body=json.loads((FX / f"{n}.json").read_text()), id=f"sha256:{n}", labels=["real"])
            for n in NAMES}  # fmt: skip
    ctx = NS(emit=lambda *a, **k: None, review=NS(execution_id=9))
    res = ReportStage().run(ctx, arts)
    assert res.status == "succeeded", res.reason
    body = res.outputs["report"]
    assert Path(body["path"]).read_text() == body["html"] and body["cli"].startswith("P0 defects")


def test_product_section_lists_defects_problem_tests_and_improvements():
    """M8 report D13: built from records; reasons are templated per status."""
    from tp.stages.report import product

    spec = {"criteria": [{"id": "C-1", "title": "Discount is 0-100"},
                         {"id": "C-2", "title": "Price rounds half-up", "status": "assumed",
                          "review_needed": True}],
            "decisions": [{"about": "rounding", "answer": "half-up", "assumed": True}]}  # fmt: skip
    cases = {"cases": [{"id": "TC-C1-1", "purpose": "Discount of 100 is accepted",
                        "defect_hypothesis": "an exclusive upper bound", "ac": "C-1"}],
             "signoff": {"approved": 1, "lines": 1}}  # fmt: skip
    gate = {"rows": [{"node": "t1", "unit": "TC-C1-1", "case_id": "TC-C1-1.b", "ac": "C-1",
                      "status": "CANDIDATE_DEFECT"},
                     {"node": "t2", "unit": "TC-C1-1", "case_id": "TC-C1-1.a", "ac": "C-1",
                      "status": "WEAK"}],
            "uncovered_criteria": ["C-2"], "settings": {"k": 5},
            "survivors": [{"class": "test_gap", "file": "m.py", "line": 3, "diff": "-a\n+b",
                           "criteria": ["C-1"]}]}  # fmt: skip
    tri = {"defects": [{"id": "D-reference/C-1", "kind": "real", "class": "PRODUCT_DEFECT",
                        "ac": "C-1", "cases": ["TC-C1-1.b"], "evidence": ["ex:1"],
                        "suspects": [{"file": "m.py", "line": 3, "text": "le=99", "basis": "b"}],
                        "suggestion": {"explanation": "x", "patch": "p", "applies": True}}],
           "findings": [{"variant": "reference", "node": "t1", "case_id": "TC-C1-1.b",
                         "ac": "C-1", "class": "PRODUCT_DEFECT", "rule": "r",
                         "evidence": ["ex:1"]}]}  # fmt: skip
    p = product.build(spec, cases, gate, tri, {"rules": {"clarify": ["money: rounding"]}})
    assert p["defects"][0]["location"]["file"] == "m.py"
    assert p["defects"][0]["tests"][0]["title"] == "Discount of 100 is accepted"
    assert [(t["status"], t["source"]) for t in p["problematic_tests"]] == [
        ("PRODUCT_DEFECT", "execute"),
        ("WEAK", "gate"),
    ]
    assert {i["kind"] for i in p["improve"]} == {"uncovered", "survivor", "assumed", "spec_gap"}
    assert p["rules"]["clarify"] == ["money: rounding"] and p["summary"]["defects"] == 1
    assert p["causes"][0]["requirements"] == [{"id": "D-reference/C-1", "ac": "C-1",
                                               "criterion": "Discount is 0-100"}]  # fmt: skip


def test_defects_with_one_cause_merge_and_duplicate_rows_collapse():
    from tp.stages.report import product

    spec = {"criteria": [{"id": "C-1", "title": "a"}, {"id": "C-2", "title": "b"}]}
    loc = [{"file": "items.py", "line": 75, "text": "", "basis": ""}]
    obs = {"method": "PUT", "path": "/x", "status": 500}
    tri = {"defects": [{"id": f"D-reference/C-{i}", "kind": "real", "class": "PRODUCT_DEFECT",
                        "ac": f"C-{i}", "cases": [], "evidence": [f"ex:{i}"], "suspects": loc,
                        "observed": obs, "suggestion": {"applies": i == 2, "patch": "p"}}
                       for i in (1, 2)],
           "findings": [{"variant": "reference", "node": n, "case_id": "TC-C1-1.a" if n == "x"
                         else "TC-C1-1.b", "ac": "C-1", "class": "PRODUCT_DEFECT", "rule": "r",
                         "evidence": [n]} for n in ("x", "y")]}  # fmt: skip
    cases = {
        "cases": [{"id": "TC-C1-1", "purpose": "bound", "defect_hypothesis": "h", "ac": "C-1"}]
    }
    p = product.build(spec, cases, {"rows": []}, tri, {})
    assert (
        len(p["causes"]) == 1 and p["causes"][0]["id"] == "D-reference/C-2"
    )  # the one that applies
    assert [r["ac"] for r in p["causes"][0]["requirements"]] == ["C-1", "C-2"]
    assert [(t["title"], t["count"]) for t in p["problematic_tests"]] == [("bound", 2)]


def test_request_bodies_in_the_worklist_are_not_counts():
    """M8: a failing request with a numeric body (price, discount) must not trip the R1 check."""
    from tp.stages.report import build

    assert list(build.walk({"worklist": {"P0": [{"observed": {"json": {"price": 5.0}}}]}})) == []
    assert list(build.walk({"n_tests": 3})) == [".n_tests"]


def test_generic_checks_section_ceiling_and_unbound_reasons():
    """checks/001 D6, D9, D12, D13 on a hand-made black-box run."""
    rows = [{"node": f"test_tp_families.py::test_generic[{c}]", "unit": c, "ac": c, "case_id": c,
             "status": s, **({"excluded": x} if x else {})}
            for c, s, x in (("G:properties:GET /a", "ADMITTED_UNMEASURED", None),
                            ("G:properties:GET /b", "SPEC_MISMATCH", None),
                            ("G:read_back:POST /a", "EXCLUDED", "async_create: no id"))]  # fmt: skip
    gate = {"rows": rows, "mutants": {"strength": "not_measured"}}
    tri = {"findings": [{"id": "F-reference/G:properties:GET /b", "class": "SPEC_MISMATCH",
                         "variant": "reference", "ac": "G:properties:GET /b", "case_id": rows[1]["case_id"],
                         "rule": "r", "dist": "3/3", "evidence": ["ex:1"],
                         "observed": {"request": "GET /b", "status": 404, "checks": ["Undocumented status code"],
                                      "curl": "curl -X GET http://t/b"}}],
           "defects": []}  # fmt: skip
    cases = {"cases": [{"id": "TC-1", "ac": "C-1"}]}
    bound = {"tests": [{"unit": "TC-1", "status": "UNBOUND", "reason": "no route for the upload"}]}
    r = build.build({"criteria": []}, cases, gate, {"verdicts": []}, tri, [], [], bound,
                    {"reach": "container", "mutation": "none"})  # fmt: skip
    assert r["header"]["ceiling"] == "Stable, property-checked"
    c = r["checks"]
    assert c["families"]["properties"]["outcomes"]["SPEC_MISMATCH"]["n"] == 1
    assert c["excluded"]["async_create"]["n"] == 1
    assert c["spec_mismatch"][0]["observed"]["status"] == 404
    assert r["worklist"]["P1"][0]["closes_it"] == "bind TC-1: no route for the upload"
    assert (
        list(
            build.walk(
                {k: v for k, v in r.items() if k not in ("evidence", "defects", "traceability")}
            )
        )
        == []
    )
    page = render.html(r)
    assert "Server and spec disagree (1 operations)" in page and "curl -X GET http://t/b" in page
    assert "Strongest claim: Stable, property-checked." in render.summary_md(r)
