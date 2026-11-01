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


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))


@pytest.fixture(scope="module")
def report():
    b = {n: json.loads((FX / f"{n}.json").read_text()) for n in NAMES}
    return build.build(
        b["spec"], b["cases"], b["gate_report"], b["run_records"], b["triage"], [], ["real"]
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
