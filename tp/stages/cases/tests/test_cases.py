"""Cases stage with a scripted model (no API key): C1–C5 behaviour."""

import json
import sys
from pathlib import Path

import pytest

from tp.core import artifacts, llm, review, runner
from tp.stages.cases import ai, build, signals

ITEMS, ITEM = "POST /api/v1/items/", "GET /api/v1/items/{id}"
SPEC = {
    "sut": {"name": "fake", "ref": "a" * 40}, "findings": [],
    "criteria": [
        {"id": "C-11", "title": "Title has a maximum length of 255 characters", "actor": "user",
         "given": "a user", "when": "creates an item", "status": "confirmed", "status_code": None,
         "then": "titles longer than 255 characters are refused", "routes": [ITEMS]},
        {"id": "C-8", "title": "User cannot see other users' items", "actor": "user",
         "given": "another user's item", "when": "a user gets it", "status": "confirmed",
         "status_code": 403, "then": "403 is returned", "routes": [ITEM]},
        {"id": "O-6", "title": "Negative skip causes a server error", "actor": "user",
         "given": "a user", "when": "lists with skip=-1", "status": "confirmed",
         "status_code": 422, "then": "422 validation error for negative skip or limit",
         "suspected_defect": True, "routes": ["GET /api/v1/items/"]},
        {"id": "C-15", "title": "Default page size", "actor": "user", "given": "a user",
         "when": "lists items", "status": "assumed", "status_code": None,
         "then": "items are returned", "resolution": "100 (current)",
         "routes": ["GET /api/v1/items/"]},
        {"id": "C-1", "title": "Login returns a token", "actor": "user", "given": "a user",
         "when": "logs in", "status": "confirmed", "status_code": None,
         "then": "a token is returned", "routes": ["POST /api/v1/login/access-token"]},
    ],
}  # fmt: skip


def case(technique, hyp, expected="2xx", kind="literal", calculator="", params=()):
    return {"technique": technique, "purpose": hyp, "defect_hypothesis": hyp, "input_class": hyp,
            "preconditions": ["user"], "steps": ["call it"], "data": [],
            "expected_kind": kind, "expected_value": expected, "calculator": calculator,
            "params": [{"name": k, "value": v} for k, v in params]}  # fmt: skip


DRAFT = {"criteria": [
    {"criterion": "C-11", "extra_templates": [], "skipped_templates": [],
     "cases": [case("acceptance", "overlong title is accepted", expected="4xx"),
               case("error_exception", "overlong title stored", expected="409")],
     "boundaries": [{"defect_hypothesis": "off by one at 255", "input_class": "title length",
                     "preconditions": ["user"], "steps": ["post"], "field": "title",
                     "limit": 255, "inclusive": True, "direction": "max", "inclusive_reason": "",
                     "ok": "2xx",
                     "fail": "4xx"}], "tables": []},
    {"criterion": "C-8", "extra_templates": [], "skipped_templates": [],
     "cases": [case("acceptance", "owner check missing", expected="403"),
               case("error_exception", "owner check missing", expected="403")],  # duplicate
     "boundaries": [],
     "tables": [{"defect_hypothesis": "wrong actor allowed", "input_class": "actor",
                 "preconditions": ["user", "item"], "steps": ["get"],
                 "conditions": ["actor", "owns"],
                 "rows": [{"values": ["user", "no"], "expected": "403"},
                          {"values": ["superuser", "no"], "expected": "2xx"},
                          {"values": ["user", "yes"], "expected": "2xx"}]}]},
    {"criterion": "O-6", "extra_templates": [], "skipped_templates":
        [{"template": "boundary", "reason": "covered by error guessing"}],
     "cases": [case("acceptance", "negative skip not validated", expected="422"),
               case("error_guessing", "negative limit not validated", expected="422"),
               case("error_exception", "error leaks details", expected="500")],  # untraceable
     "boundaries": [], "tables": []},
    {"criterion": "C-15", "extra_templates": [], "skipped_templates": [],
     "cases": [case("acceptance", "default page size wrong", kind="calculator",
                    calculator="page_window", params=[("total", "150"), ("skip", "0"),
                                                      ("limit", "100")])] +
              [case("error_guessing", f"extra {i}") for i in range(4)],  # 4 extras: 1 over
     "boundaries": [], "tables": []},
]}  # fmt: skip


PROMPTS: list[str] = []  # drafting prompts seen, newest last


def fake_model(prompt, model, params):
    assert model == ai.MODEL
    if params["schema"] == ai.DRAFT_SCHEMA:
        PROMPTS.append(prompt)
        return json.dumps(DRAFT), {"input_tokens": 3000, "output_tokens": 2000}
    rejected = json.loads(prompt.split("<rejected>")[1].split("</rejected>")[0])
    out = [
        {
            **case("acceptance", "title over the limit is refused (revised)", expected="4xx"),
            "id": r["id"],
        }
        for r in rejected
    ]
    return json.dumps({"cases": out, "boundaries": [], "tables": []}), {}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(llm, "anthropic_provider", fake_model)


def _run(spec=SPEC, options=None):
    art = artifacts.put("spec", spec)
    run_id = runner.start_run("record", stub=False, options=options)
    ex = runner.rerun(run_id, "cases", overrides={"spec": art.id})
    return run_id, ex


def _cases(run_id):
    ex = [e for e in runner.get_run(run_id)["executions"] if e["stage"] == "cases"][-1]
    return ex, (artifacts.get(ex["outputs"]["cases"]) if ex["outputs"] else None)


def _signoff():
    return next(i for i in review.list_(status="open") if i["kind"] == "signoff")


def _approve_all(item, reject=None):
    lines = {ln["id"]: {"decision": "approve", "comment": ""} for ln in item["payload"]["lines"]}
    for lid, comment in (reject or {}).items():
        lines[lid] = {"decision": "reject", "comment": comment}
    review.resolve(item["id"], None, "po", detail={"lines": lines})
    return runner.resume(item["id"])


def test_a_criterion_key_with_its_title_still_matches(model, monkeypatch):
    """Real run r-0031: Haiku answered "C-5 Expired token is refused" for "C-5" → 0 cases."""
    titled = DRAFT | {
        "criteria": [d | {"criterion": f"{d['criterion']} some title"} for d in DRAFT["criteria"]]
    }
    monkeypatch.setattr(sys.modules[__name__], "DRAFT", titled)
    _run_id, ex = _run()
    assert ex["status"] == "awaiting_review", ex["reason"]
    assert not any("returned no cases" in a for a in _signoff()["payload"]["attention"])


def test_minimum_template_sets_are_deterministic_for_the_fixture_spec():
    spec = json.loads((Path(__file__).parents[1] / "fixtures/spec.json").read_text())
    first = {c["id"]: signals.minimum_templates(c) for c in spec["criteria"]}
    assert first == {c["id"]: signals.minimum_templates(c) for c in spec["criteria"]}
    assert "boundary" in first["C-11"] and "error_guessing" in first["O-6"]
    assert "decision_table" in first["C-8"]


def test_flows_by_route_prefix():
    f = {c["id"]: signals.flow_of(c) for c in SPEC["criteria"]}
    assert f == {"C-11": "FLOW-items-crud", "C-8": "FLOW-items-crud", "O-6": "FLOW-items-crud",
                 "C-15": "FLOW-items-crud", "C-1": "FLOW-signup-login"}  # fmt: skip


def test_draft_builds_traced_ranked_cases(model):
    run_id, ex = _run()
    assert ex["status"] == "awaiting_review", ex["reason"]
    assert "Login returns a token" not in PROMPTS[-1]  # out-of-scope flow never drafted
    item = _signoff()
    lines = item["payload"]["lines"]
    attention = " ".join(item["payload"]["attention"])
    _approve_all(item)  # assertions below read the signed artifact, not the stage's scratch file
    _, art = _cases(run_id)
    units = {u["id"]: u for u in art.body["cases"]}

    # C2: one boundary group with 254/255/256, sides computed by the calculator
    (b,) = [u for u in units.values() if u["technique"] == "boundary"]
    assert [(p["value"], p["expected"]["value"]) for p in b["points"]] == [
        (254, "2xx"), (255, "2xx"), (256, "4xx")]  # fmt: skip
    assert item["payload"]["worked_examples"][0]["id"] == b["id"]
    # C3: every expected value traces to a calculator or a literal from the spec
    page = next(u for u in units.values() if u["ac"] == "C-15" and u["technique"] == "acceptance")
    assert page["expected"] == {"value": {"count": 150, "data_len": 100},
                                "oracle": "calculator:page_window",
                                "params": {"total": "150", "skip": "0", "limit": "100"}}  # fmt: skip
    overlong = next(u for u in units.values() if u["purpose"] == "overlong title stored")
    assert overlong["expected"]["value"] == "4xx" and overlong["notes"]  # 409 -> stated class
    assert "O-6: dropped a drafted case: expected '500' is not traceable" in attention
    assert "C-8: decision table leaves out 1 combination(s)" in attention
    # C4: duplicates and the over-budget extra are deferred, never silently dropped
    reasons = [d["deferred"] for d in art.body["deferred"]]
    assert any(r.startswith("duplicate") for r in reasons)
    assert "over the extras budget for this criterion" in reasons
    hyps = [u["defect_hypothesis"].lower() for u in units.values()]
    assert len(hyps) == len(set(hyps))
    # Ranker: the suspected defect comes first; login (other flow) is listed as not tested
    assert lines[0]["id"].startswith("TC-O6") and lines[0]["text"].startswith("P0")
    assert {"criterion": "C-1", "reason": "flow FLOW-signup-login not in this run"} in \
        item["payload"]["not_tested"]  # fmt: skip


def test_rejecting_one_line_reopens_only_that_line_then_signs(model):
    run_id, _ = _run()
    first = _signoff()
    target = next(ln["id"] for ln in first["payload"]["lines"] if ln["id"].startswith("TC-C11"))
    _approve_all(first, reject={target: "title should be checked on update too"})
    second = _signoff()
    assert [ln["id"] for ln in second["payload"]["lines"]] == [target]
    _approve_all(second)
    ex, art = _cases(run_id)
    assert ex["status"] == "succeeded", ex["reason"]
    assert art.body["signoff"]["signed"] and art.body["signoff"]["hash"]
    assert "unsigned" not in art.labels
    revised = next(c for c in art.body["cases"] if c["id"] == target)
    assert revised["quality"] == "revised"


def test_unchanged_approved_lines_are_carried_forward(model):
    _run_id, _ = _run()
    _approve_all(_signoff())
    run2, ex2 = _run()  # same spec, same draft -> same lines
    assert ex2["status"] == "succeeded", ex2["reason"]
    _, art = _cases(run2)
    assert art.body["signoff"]["signed"]
    assert len(art.body["signoff"]["carried_forward"]) == art.body["signoff"]["lines"]


def test_deferring_the_signoff_labels_cases_unsigned(model):
    run_id, _ = _run()
    item = _signoff()
    review.resolve(item["id"], "defer", "po")
    runner.resume(item["id"])
    _, art = _cases(run_id)
    assert "unsigned" in art.labels and not art.body["signoff"]["signed"]


def test_literal_outcomes_must_be_stated_by_the_spec():
    c = SPEC["criteria"][0]
    assert build.literal(c, "4xx") == ("4xx", None)
    assert build.literal(c, "422")[0] == "4xx"
    with pytest.raises(ValueError):
        build.literal({**c, "then": "the item has a title"}, "500")


def test_table_rows_may_trace_to_sibling_criteria_on_the_same_route():
    refused = {"id": "C-8", "title": "others cannot see items", "then": "403", "status_code": 403,
               "routes": [ITEM]}  # fmt: skip
    owner = {"id": "C-7", "title": "owner can see items", "then": "the item is returned",
             "status_code": None, "routes": [ITEM]}  # fmt: skip
    other_route = {"id": "C-6", "title": "x", "then": "201 created", "status_code": 201,
                   "routes": [ITEMS]}  # fmt: skip
    view = build.with_siblings(refused, [refused, owner, other_route])
    assert {"403", "2xx"} <= build.traceable(view) and "201" not in build.traceable(view)


def test_prose_after_a_code_is_a_note_not_the_oracle():
    c = {"id": "O-8", "title": "others get 403", "then": "403 Not enough permissions",
         "status_code": 403, "routes": []}  # fmt: skip
    assert build.literal(c, "403 'Not enough permissions'") == (
        "403", "message 'Not enough permissions' stated by the spec")  # fmt: skip
    assert build.literal(c, "403: forbidden, go away")[1].endswith("not checked")


def test_carry_forward_is_scoped_to_the_sut_ref_and_off_when_fresh(model):
    _run()
    _approve_all(_signoff())
    other_ref = {**SPEC, "sut": {"name": "fake", "ref": "b" * 40}}
    for spec, options in ((other_ref, None), (SPEC, {"fresh": True})):
        _, ex = _run(spec, options)  # same lines, but another SUT ref / a fresh run
        assert ex["status"] == "awaiting_review"  # asked again, not carried forward
        review.resolve(_signoff()["id"], "defer", "po")


def test_the_flows_option_changes_the_scope(model):
    run_id, _ = _run(options={"flows": ["FLOW-signup-login"]})
    assert "Login returns a token" in PROMPTS[-1] and "Title has a maximum" not in PROMPTS[-1]
    _, art = _cases(run_id)  # the scripted draft has no login cases: nothing to sign
    assert art.body["flows"] == ["FLOW-signup-login"]
    assert "unsigned" in art.labels and not art.body["signoff"]["signed"]
    reasons = {n["criterion"]: n["reason"] for n in art.body["not_tested"]}
    assert reasons["C-11"] == "flow FLOW-items-crud not in this run"
