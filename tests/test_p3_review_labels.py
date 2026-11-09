"""P3 (docs/status/cohesion/001): brief -> cases on a black-box target, the Sign-off listing, the
claim-first header and the dependency ledger. Synthetic profiles in the C5 shapes; the model is a
scripted fake (K5). No Docker."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tp.api.app import app, worker
from tp.core import artifacts, llm, review, runner
from tp.stages.cases import ai
from tp.stages.cases.stage import _default_flows
from tp.stages.report import build, render

FX = Path(__file__).parents[1] / "tp/stages/report/fixtures"
TEMPLATE = Path(__file__).parents[1] / "targets/fastapi-template"
WEBHOOK = {
    "name": "webhook",
    "kind": "http",
    "via": "env",
    "key": "NOTIFY_URL",
    "status": "wired",
    "reason": None,
    "evidence": "env NOTIFY_URL",
    "trigger": "POST /api/notes",
    "setup": None,
}
INVITE = {"name": "mail", "kind": "smtp", "via": "env", "key": "SMTP_HOST", "status": "unwired",
          "reason": "no operation reached the stand-in", "evidence": "env SMTP_HOST"}  # fmt: skip


def _field(value: dict) -> dict:
    return {"source": "probed", "value": value}


def profile_yaml(reach: dict, checks: dict | None = None) -> dict:
    """A black-box profile (synthetic, the notify fixture's shape): no source, no flows."""
    return {"schema_version": 1, "commit_tested": "c" * 40,
            "target": {"name": "notify", "repo": "tests/fixtures/sut/notify", "type": "api"},
            "reach": _field(reach), "spec": _field({"strategy": "served", "openapi": "/openapi.json"}),
            "auth": _field({"strategy": "token_header"}), "data": _field({"strategy": "spec_derived"}),
            "state": _field({"strategy": "namespace"}), "mutation": _field({"strategy": "none"}),
            "safety": _field({"strategy": "unrestricted"}),
            "checks": _field({"strategy": "pytest_cases", **(checks or {})})}  # fmt: skip


CONTAINER = {"strategy": "container", "build": {"dockerfile": "Dockerfile", "context": ".", "port": 8080},
             "standins": [{"name": "webhook", "kind": "http", "image": "runner", "port": 8080,
                           "journal": "/__journal", "env": {"NOTIFY_URL": "http://tp-standin-webhook:8080/notify_url"}}]}  # fmt: skip
CHECKS = {"dependencies": [WEBHOOK, INVITE], "families": {"dependencies": [WEBHOOK]},
          "ui": [{"name": "create-note", "steps": [{"fill": "title"}, {"click": "save"}],
                  "verified": {"op": "POST /api/notes", "read_back": "GET /api/notes/{id}"}}]}  # fmt: skip


def put_target(data: Path, slug: str, reach: dict, checks: dict | None = None) -> Path:
    d = data / "targets" / slug
    d.mkdir(parents=True)
    (d / "profile.yaml").write_text(yaml.safe_dump(profile_yaml(reach, checks)))
    return d


# ---- 1. a one-line brief on a black-box target reaches cases ----------------------------------

# What the spec stage made of the one-line brief "users keep notes" (spec stage output, synthetic)
SPEC = {"sut": {"name": "notify", "ref": "c" * 40}, "findings": [], "index": {"from": "openapi"},
        "criteria": [
            {"id": "C-1", "title": "A user creates a note", "actor": "user", "given": "a user",
             "when": "creates a note", "then": "the note is returned", "status": "confirmed",
             "status_code": 201, "routes": ["POST /api/notes"]},
            {"id": "C-2", "title": "A user reads a note back", "actor": "user", "given": "a note",
             "when": "reads it", "then": "the same note is returned", "status": "confirmed",
             "status_code": 200, "routes": ["GET /api/notes/{id}"]},
            {"id": "C-3", "title": "Notes are kept", "actor": "user", "given": "a user",
             "when": "comes back", "then": "the notes are still there", "status": "assumed",
             "status_code": None, "routes": []}]}  # fmt: skip


def _case(hyp: str) -> dict:
    return {"technique": "acceptance", "purpose": hyp, "defect_hypothesis": hyp, "input_class": hyp,
            "preconditions": [], "steps": ["call it"], "data": [], "expected_kind": "literal",
            "expected_value": "2xx", "calculator": "", "params": []}  # fmt: skip


def fake_model(prompt, model, params):
    assert model == ai.MODEL and params["schema"] == ai.DRAFT_SCHEMA
    ids = [c["id"] for c in SPEC["criteria"] if f'"{c["id"]}"' in prompt]
    return json.dumps({"criteria": [
        {"criterion": i, "extra_templates": [], "skipped_templates": [], "boundaries": [],
         "tables": [], "cases": [_case(f"{i} not met")]} for i in ids]}), {}  # fmt: skip


def test_a_one_line_brief_on_a_black_box_target_reaches_cases(tmp_path, monkeypatch):
    target = put_target(tmp_path, "notify", CONTAINER, CHECKS)
    monkeypatch.setenv("TP_TARGET", str(target))
    monkeypatch.setattr(llm, "anthropic_provider", fake_model)
    assert _default_flows(("FLOW-items-crud",)) is None  # the template's default names no flow here
    run_id = runner.start_run("record", stub=False)
    ex = runner.rerun(run_id, "cases", overrides={"spec": artifacts.put("spec", SPEC).id})
    assert ex["status"] == "awaiting_review", ex["reason"]
    [item] = [i for i in review.list_(status="open") if i["kind"] == "signoff"]
    p = item["payload"]
    assert {ln["ac"] for ln in p["lines"]} == {"C-1", "C-2"}
    assert p["not_tested"] == []  # none is "flow … not in this run"
    # C-3 maps to no route: it is drafted (in scope), and its case is dropped by the trace rule
    assert "C-3: no acceptance case" in p["attention"]
    # Sign-off lists what else runs, read-only, each with its basis (DR-11, DR-7)
    also = {a["id"]: a for a in p["also_runs"]}
    assert {"properties", "auth_required", "paging"} <= set(also)
    assert also["dependency_called:webhook"]["basis"] == "simulated"
    assert "tp-standin-webhook" in also["dependency_failure_handled:webhook"]["text"]
    assert also["U:create-note"] == {"kind": "ui", "id": "U:create-note", "basis": "real",
                                     "text": "UI flow create-note: 2 steps"}  # fmt: skip
    assert not [a for a in also if "mail" in a]  # unwired: it won't run (the ledger says why)
    assert "also_runs" not in json.dumps(p["lines"])  # not signed


def test_the_template_keeps_its_default_scope(monkeypatch):
    monkeypatch.setenv("TP_TARGET", str(TEMPLATE))
    assert _default_flows(("FLOW-items-crud",)) == ("FLOW-items-crud",)


# ---- 2. the claim-first header, over HTTP ------------------------------------------------------


@pytest.fixture
def client():
    yield TestClient(app)
    worker.submit(lambda: None).result(timeout=30)


def _claim(client, profile: str) -> dict:
    run = client.post("/runs", json={"stub": True, "profile": profile}).json()
    worker.submit(lambda: None).result(timeout=30)
    return client.get(f"/runs/{run['id']}/claim").json()


def test_the_header_for_template_container_and_external_runs(client, tmp_path):
    put_target(tmp_path, "notify", CONTAINER, CHECKS)
    put_target(tmp_path, "live", {"strategy": "external", "base_url": "http://127.0.0.1:8010"})
    got = {p: _claim(client, p) for p in ("fastapi-template", "notify", "live")}
    pick = lambda c: (c["target"], c["reach"], c["isolation"], c["ceiling"])
    assert pick(got["fastapi-template"]) == ("fastapi-template", "container", 0,
                                             "Likely defect, catches planted bugs")  # fmt: skip
    assert pick(got["notify"]) == ("notify", "container", 1, "Stable")
    assert pick(got["live"]) == ("live", "external", 2, "Seen on the live system, stable K/K")
    assert got["notify"]["isolation_words"] == "partly isolated (stand-ins)"
    assert all(c["ran"] is None for c in got.values())  # stub runs have no gate rows
    assert client.get("/runs/999/claim").status_code == 404


def test_what_ran_what_was_blocked_and_what_it_rests_on():
    gate = {"rows": [
        {"case_id": "TC-1", "unit": "TC-1", "status": "ADMITTED"},
        {"case_id": "G:dependency_called:POST /api/notes", "unit": "generic", "status": "ADMITTED"},
        {"case_id": "G:paging:GET /api/notes", "unit": "generic", "status": "EXCLUDED",
         "excluded": "setup_refused: POST /api/notes answered 403"},
        {"case_id": "TC-2", "unit": "TC-2", "status": "REJECTED"},
        {"case_id": "TC-3", "unit": "TC-3", "status": "ADMITTED"}]}  # fmt: skip
    v = lambda c, verdict, a: {
        "case_id": c,
        "variant": "reference",
        "verdict": verdict,
        "attempts": a,
    }
    rr = {"verdicts": [v("TC-1", "PASS", ["ex:1"]), v("G:dependency_called:POST /api/notes", "PASS", ["ex:2"]),
                       v("TC-3", "NOT_RUN", ["ex:3"])]}  # fmt: skip
    records = {"ex:1": {"id": "ex:1", "exchanges": [{"path": "/api/notes"}]},
               "ex:2": {"id": "ex:2", "exchanges": [{"path": "http://tp-standin-webhook:8080/__journal"}]}}  # fmt: skip
    cases = {"cases": [{"id": "TC-1"}, {"id": "TC-2"}, {"id": "TC-3"}, {"id": "TC-4"}]}
    target = {"slug": "notify", "reach": "container", "isolation": 1}
    c = build.claim(target, gate, rr, cases, records, {"reach": "container"})
    assert (c["ran"]["n"], c["ran"]["d"]) == (2, 6)  # 5 rows + TC-4, signed but never bound
    assert {b["why"]: b["cases"]["ids"] for b in c["blocked"]} == {
        "excluded: setup_refused": ["G:paging:GET /api/notes"], "rejected at the gate": ["TC-2"],
        "not run: NOT_RUN": ["TC-3"], "signed case not bound to a test": ["TC-4"]}  # fmt: skip
    assert {k: n["ids"] for k, n in c["basis"].items()} == {
        "real": ["TC-1"],
        "simulated": ["G:dependency_called:POST /api/notes"],
    }
    assert list(build.walk(c)) == []  # every count carries its composition
    line = render.claim_line(c)
    assert line.startswith(
        "notify · container · isolation 1 (partly isolated (stand-ins)) · Stable · ran 2 of 6"
    )
    assert "blocked ▸ excluded: setup_refused 1/6" in line and "simulated 1/2" in line
    assert build.basis(["ex:9"], records) is None  # no record: not stated, never guessed


# ---- 3. the dependency ledger, rendered from a fixture -----------------------------------------

SEEN = {"G:dependency_called:POST /api/notes": {"outcome": "passed", "fault": None,
                                                 "received": [{"method": "POST", "path": "/notify_url",
                                                               "body": {"event": "note.created"}}]},
        "G:dependency_failure_handled:POST /api/notes": {"outcome": "failed",
                                                         "fault": {"standin": "webhook", "mode": "delay", "value": 8.0},
                                                         "received": []}}  # fmt: skip


def test_the_ledger_lists_every_dependency_tested_or_not(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TARGET", str(put_target(tmp_path, "notify", CONTAINER, CHECKS)))
    run = tmp_path / "runs" / "execute-1"
    run.mkdir(parents=True)
    (run / "dependencies.json").write_text(json.dumps(SEEN))
    (run / "results.jsonl").write_text(
        json.dumps(
            {"id": "ex:1", "fault": SEEN["G:dependency_failure_handled:POST /api/notes"]["fault"]}
        )
        + "\n"
    )
    facts = build.run_facts({"folder": "runs/execute-1"})
    assert facts["target"]["isolation"] == 1 and facts["seen"] == SEEN
    assert build.basis(["ex:1"], {r["id"]: r for r in facts["records"]}) == "simulated"

    b = {n: json.loads((FX / f"{n}.json").read_text())
         for n in ("spec", "cases", "gate_report", "run_records", "triage")}  # fmt: skip
    r = build.build(b["spec"], b["cases"], b["gate_report"], b["run_records"], b["triage"], [],
                    ["real"], facts=facts)  # fmt: skip
    hook, mail = r["dependencies"]
    assert (hook["status"], hook["standin"], hook["basis"]) == (
        "tested",
        "tp-standin-webhook",
        "simulated",
    )
    assert [c["outcome"] for c in hook["checks"]] == ["passed", "failed"]
    assert hook["received"] == SEEN["G:dependency_called:POST /api/notes"]["received"]
    assert mail == {"name": "mail", "kind": "smtp", "via": "env", "key": "SMTP_HOST", "trigger": None,
                    "status": "not tested", "reason": "no operation reached the stand-in"}  # fmt: skip
    assert (
        list(
            build.walk(
                {k: v for k, v in r.items() if k not in ("evidence", "defects", "traceability")}
            )
        )
        == []
    )
    page, md = render.html(r), render.summary_md(r)
    assert "<h2>Outbound dependencies</h2>" in page and "tp-standin-webhook" in page
    assert "fault delay 8.0" in page and "not tested: no operation reached the stand-in" in page
    assert "- mail (smtp): not tested: no operation reached the stand-in" in md
    assert "<p class=claim><b>notify · container · isolation 1" in page


def test_wired_but_not_run_says_so():
    [d] = build.ledger([WEBHOOK], {})
    assert d["reason"] == "wired, but no dependency check ran on it in this run"


# ---- 4. an empty brief is a Scan of the verified spec ------------------------------------------


def test_an_empty_brief_is_a_scan(client):
    run = client.post(
        "/runs", json={"stub": True, "requirements": {"kind": "brief", "text": " "}}
    ).json()
    heads = runner.heads(run["id"])
    assert artifacts.get(heads["requirements"]).body["text"] == "Scan of the whole verified spec"
    assert "whole_spec" in artifacts.get(heads["run_options"]).body["scan"]
    bad = client.post("/runs", json={"stub": True, "requirements": {"kind": "story", "text": ""}})
    assert bad.status_code == 422
