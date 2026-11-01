"""Triage: one test per hard rule, the evidence index, the localiser chain (J2), the scorer
(J3) and an override (J4). Inputs are real records/log excerpts from execute run x230."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tp.core import db, review
from tp.stages.triage import localise, rules
from tp.stages.triage.evidence import EvidenceIndex
from tp.stages.triage.stage import TriageStage, analyse

FX = Path(__file__).parents[1] / "fixtures"
ITEMS = "backend/app/api/routes/items.py"
PAGINATION_TRACE = "x230.reference.a1/TC-O6-2.a/7"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))


@pytest.fixture(scope="module")
def spec():
    return json.loads((FX / "spec.json").read_text())


def read(rel):
    if rel == ITEMS:
        return (FX / "items.patched.py.txt").read_text(), (FX / "items.upstream.py.txt").read_text()
    return None


def row(
    rid, outcome="failed", origin="PRODUCT_ERROR", failed=("TC/status",), trace=None, query=None
):
    ex = {
        "step": 7,
        "setup": False,
        "method": "GET",
        "path": "/api/v1/items/",
        "query": query or {},
        "json": None,
        "status": 500 if origin == "PRODUCT_ERROR" else 200,
        "x_test_trace": trace,
    }
    return {"id": rid, "status": "RAN", "outcome": outcome, "origin": origin,
            "assertions": [{"id": a, "ok": False} for a in failed] if outcome == "failed" else [],
            "exchanges": [ex]}  # fmt: skip


def vd(
    case,
    variant="reference",
    verdict="CONSISTENT_FAIL",
    gate="CANDIDATE_DEFECT",
    ac="O-6",
    n=3,
    **kw,
):
    return {"node": f"t.py::{case}", "case_id": case, "ac": ac, "variant": variant, "verdict": verdict,
            "gate_status": gate, "dist": "3/3", "attempts": [f"ex:{variant}/a{i}/{case}" for i in range(1, n + 1)], **kw}  # fmt: skip


def rows_for(v, **kw):
    return {a: row(a, **kw) for a in v["attempts"]}


# ---- HardRules: one test per rule, in order -------------------------------------------------


def test_env_rule():
    assert rules.classify(vd("T", verdict="SUSPECT_ENV"), None, {})[0] == "ENV"
    assert (
        rules.classify(vd("T", verdict="NOT_RUN", reason="ENV: pre-probe failed"), None, {})[0]
        == "ENV"
    )


def test_data_rule():
    v = vd("T")
    assert rules.classify(v, None, rows_for(v, origin="DATA"))[0] == "DATA"


def test_flaky_test_vs_intermittent_product():
    v = vd("T", verdict="INTERMITTENT", escalation={"escalated": True})
    assert rules.classify(v, None, rows_for(v, origin="BINDING"))[0] == "FLAKY_TEST"
    assert rules.classify(v, None, rows_for(v, origin="ASSERTION"))[0] == "INTERMITTENT_PRODUCT"
    v["escalation"]["escalated"] = False
    assert rules.classify(v, None, rows_for(v, origin="ASSERTION"))[0] == "INSUFFICIENT_EVIDENCE"


def test_test_defect_rule():
    v = vd("T")
    assert rules.classify(v, None, rows_for(v, origin="PLATFORM"))[0] == "TEST_DEFECT"


def test_spec_gap_rule():
    v = vd("TC-C13-1.a", gate="SPEC_GAP", ac="C-13")
    assert rules.classify(v, None, rows_for(v))[0] == "SPEC_GAP"


def test_product_defect_and_seeded_needs_a_pass_on_reference():
    ref_fail, ref_pass = vd("T"), vd("T", verdict="PASS")
    seeded = vd("T", variant="bug:BUG_X")
    assert rules.classify(ref_fail, None, rows_for(ref_fail))[0] == "PRODUCT_DEFECT"
    assert rules.classify(seeded, ref_pass, rows_for(seeded))[0] == "PRODUCT_DEFECT"
    assert rules.classify(seeded, ref_fail, rows_for(seeded))[0] == "SAME_AS_REFERENCE"


def test_findings_drop_variant_repeats_and_group_into_defects():
    verdicts = [
        vd("TC-O6-1"),
        vd("TC-O6-2.a"),
        vd("TC-O6-1", variant="bug:BUG_X"),
        vd("TC-C8-2", ac="C-8", gate="ADMITTED"),
        vd("TC-C8-2", ac="C-8", variant="bug:BUG_X"),
    ]
    verdicts[3]["verdict"] = "PASS"
    rows = {a: row(a) for v in verdicts for a in v["attempts"]}
    fs = rules.findings(verdicts, rows)
    assert [f["id"] for f in fs] == [
        "F-reference/TC-O6-1",
        "F-reference/TC-O6-2.a",
        "F-bug:BUG_X/TC-C8-2",
    ]
    assert fs[0]["also_fails_on"] == ["bug:BUG_X"]
    ds = rules.defects(fs)
    assert [(d["id"], d["kind"], d["cases"]) for d in ds] == [
        ("D-bug:BUG_X/C-8", "seeded", ["TC-C8-2"]),
        ("D-reference/O-6", "real", ["TC-O6-1", "TC-O6-2.a"])]  # fmt: skip


def test_quarantine_only_flaky_tests_never_seeded():
    fs = [
        {"class": "FLAKY_TEST", "seeded": False, "case_id": "A", "rule": "r", "evidence": ["ex:1"]},
        {"class": "FLAKY_TEST", "seeded": True, "case_id": "B", "rule": "r", "evidence": ["ex:2"]},
        {
            "class": "INTERMITTENT_PRODUCT",
            "seeded": True,
            "case_id": "C",
            "rule": "r",
            "evidence": ["ex:3"],
        },
    ]
    q = rules.quarantine(fs, "2026-11-01T00:00:00Z")
    assert [e["case_id"] for e in q] == ["A"] and q[0]["expires"].startswith("2026-11-15")


# ---- EvidenceIndex + Localiser ---------------------------------------------------------------


def test_evidence_ids_resolve_and_a_made_up_one_does_not():
    r = row("ex:reference/a1/TC-O6-2.a", trace=PAGINATION_TRACE)
    idx = EvidenceIndex(
        [r], [{"id": "probe:reference/a1/pre"}], {"reference": (FX / "backend.log").read_text()}
    )
    assert idx.exists("ex:reference/a1/TC-O6-2.a") and idx.exists("ex:reference/a1/TC-O6-2.a#7")
    assert idx.exists("probe:reference/a1/pre") and idx.exists(f"log:{PAGINATION_TRACE}")
    assert not idx.exists("ex:reference/a1/TC-O6-2.a#99") and not idx.exists(
        "log:x9.reference.a1/TC/1"
    )
    tb = idx.log(PAGINATION_TRACE)["traceback"]
    assert tb["root_cause"]["cls"] == "psycopg.errors.InvalidRowCountInResultOffsetClause"
    assert tb["app_frames"][-1]["path"].endswith("routes/items.py")


def test_pagination_finding_is_localised_to_items_15_and_the_offset_call(spec):
    """J2: items.py:15 (signature) and the .offset(skip) call, with the traceback (2201X) as basis."""
    failing = row("ex:reference/a1/TC-O6-2.a", trace=PAGINATION_TRACE, query={"skip": "-1"})
    idx = EvidenceIndex([failing], [], {"reference": (FX / "backend.log").read_text()})
    crit = next(c for c in spec["criteria"] if c["id"] == "O-6")
    s = localise.suspects(failing, idx.log(PAGINATION_TRACE), crit, localise.Source(read))
    assert len(s) <= 5
    assert (s[0]["file"], s[0]["upstream_line"]) == (ITEMS, 15) and "skip=-1" in s[0]["basis"]
    assert "skip: int = 0" in s[0]["text"]
    assert (
        s[1]["upstream_line"] == 39 and s[1]["text"] == ".offset(skip)" and "2201X" in s[1]["basis"]
    )
    assert s[2]["upstream_line"] == 42 and "innermost app frame" in s[2]["basis"]


def test_line_map_follows_the_patch():
    up, pa = read(ITEMS)[1], read(ITEMS)[0]
    to_up, to_pa = localise.line_map(up, pa)
    assert pa.splitlines()[to_pa[15] - 1] == up.splitlines()[14]
    assert to_up[to_pa[39]] == 39


def test_scorer_ranks_the_first_suspect_in_the_flag_function_and_handler_link():
    files = {ITEMS: read(ITEMS)[0]}
    sites = localise.flag_sites(files, "BUG_OWNERSHIP_UPDATE")
    h = localise.handler(files, "PUT", "/api/v1/items/26a4b22c-7e96-47dc-b005-17b9bf47bd87")
    assert h and h[2] == "update_item"
    near, far = {"file": ITEMS, "line": h[1]}, {"file": ITEMS, "line": 1}
    assert (
        localise.rank([far, near], sites, files) == 2 and localise.rank([far], sites, files) is None
    )
    assert localise.handler(files, "GET", "/api/v1/items/")[2] == "read_items"


# ---- stage: review, override (J4) --------------------------------------------------------------


def _sealed(folder: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    seal = "".join(
        f"{hashlib.sha256((folder / r).read_bytes()).hexdigest()}  {r}\n" for r in sorted(files)
    )
    (folder / "records.sha256").write_text(seal)
    return hashlib.sha256(seal.encode()).hexdigest()


def _inputs(spec, tamper=False):
    folder = db.data_dir() / "runs" / "execute-1"
    verdicts = [vd("TC-O7-1", ac="O-7"), vd("TC-O6-2.a")]
    rows = [row(a, origin="ASSERTION") for a in verdicts[0]["attempts"]]
    rows += [row(a, trace=PAGINATION_TRACE, query={"skip": "-1"}) for a in verdicts[1]["attempts"]]
    root = _sealed(
        folder,
        {
            "results.jsonl": "".join(json.dumps(r) + "\n" for r in rows),
            "probes.json": "[]",
            "logs/reference.log": (FX / "backend.log").read_text(),
        },
    )
    if tamper:
        (folder / "results.jsonl").write_text("{}\n")
    art = lambda body: NS(body=body, id="sha256:x")
    return {
        "run_records": art({"folder": "runs/execute-1", "root": root, "verdicts": verdicts}),
        "gate_report": art({}),
        "cases": art({}),
        "spec": art(spec),
    }


def test_override_changes_the_class_and_is_recorded_with_its_reason(spec):
    ctx = NS(env=None, emit=lambda *a, **k: None, review=review.ReviewHandle(1))
    first = TriageStage().run(ctx, _inputs(spec))
    assert first.status == "awaiting_review" and len(first.review_items) == 2  # one per real defect
    items = {i["payload"]["defect"]: i for i in review.list_(execution_id=1)}
    with pytest.raises(ValueError):
        review.resolve(
            items["D-reference/O-7"]["id"], "override", "ercong", "", {"choice": "SPEC_GAP"}
        )
    review.resolve(
        items["D-reference/O-7"]["id"],
        "override",
        "ercong",
        "the story never says 422 above 100",
        {"choice": "SPEC_GAP"},
    )
    review.resolve(items["D-reference/O-6"]["id"], "accept", "ercong")
    body = TriageStage().run(ctx, _inputs(spec)).outputs["triage"]
    assert body["overrides"] == [
        {
            "defect": "D-reference/O-7",
            "from": "PRODUCT_DEFECT",
            "to": "SPEC_GAP",
            "reason": "the story never says 422 above 100",
            "by": "ercong",
            "review_item": items["D-reference/O-7"]["id"],
        }
    ]
    o7 = next(f for f in body["findings"] if f["case_id"] == "TC-O7-1")
    assert (o7["class"], o7["original_class"]) == ("SPEC_GAP", "PRODUCT_DEFECT")
    assert body["summary"] == {"SPEC_GAP": 1, "PRODUCT_DEFECT": 1}


def test_tampered_records_are_refused(spec):
    ctx = NS(env=None, emit=lambda *a, **k: None, review=review.ReviewHandle(1))
    with pytest.raises(ValueError, match="changed after sealing"):
        TriageStage().run(ctx, _inputs(spec, tamper=True))


def test_analyse_cites_only_evidence_that_exists(spec):
    inputs = _inputs(spec)
    rr = inputs["run_records"].body
    from tp.stages.triage.stage import _records

    rows, probes, logs = _records(db.data_dir() / rr["folder"], rr["root"])
    out = analyse(rr, rows, probes, logs, spec, read, {})
    o6 = next(d for d in out["defects"] if d["ac"] == "O-6")
    assert o6["evidence"] == [
        "ex:reference/a1/TC-O6-2.a",
        "ex:reference/a1/TC-O6-2.a#7",
        f"log:{PAGINATION_TRACE}",
    ]
    assert o6["suspects"][0]["upstream_line"] == 15
