import json

import pytest

from tp.core import artifacts, review, runner


def _blocked_signoff():
    run_id = runner.start_run(stub=True)
    runner.advance(run_id)
    [item] = review.list_(status="open")
    return run_id, item


def _lines(item, **overrides):
    out = {ln["id"]: {"decision": "approve"} for ln in item["payload"]["lines"]}
    return {"lines": out | overrides}


def test_stage_blocks_and_resumes_after_resolution(data):
    run_id, item = _blocked_signoff()
    assert item["kind"] == "signoff" and item["line_options"] == ["approve", "reject", "exclude"]
    review.resolve(item["id"], None, "tester", detail=_lines(item))
    assert runner.resume(item["id"])["status"] == "succeeded"
    line = json.loads((data / "decisions.jsonl").read_text().splitlines()[-1])
    assert line["decision"] == "approve" and line["by"] == "tester"
    assert line["detail"]["lines"]["TC-1"] == {"decision": "approve"}
    assert "unsigned" not in artifacts.get(runner.heads(run_id)["cases"]).labels


def test_rejecting_one_line_reopens_only_that_line():
    _, item = _blocked_signoff()
    detail = _lines(item, **{"TC-2": {"decision": "reject", "comment": "use 256, not 257"}})
    assert review.resolve(item["id"], None, "tester", detail=detail)["decision"] == "reject"
    assert runner.resume(item["id"])["status"] == "awaiting_review"
    [again] = review.list_(status="open")
    assert [ln["id"] for ln in again["payload"]["lines"]] == ["TC-2"]
    assert again["payload"]["lines"][0]["comment"] == "use 256, not 257"
    review.resolve(again["id"], None, "tester", detail=_lines(again))
    assert runner.resume(again["id"])["status"] == "succeeded"


def test_line_level_validation():
    _, item = _blocked_signoff()
    with pytest.raises(ValueError, match="missing"):
        review.resolve(item["id"], None, "t", detail={"lines": {"TC-1": {"decision": "approve"}}})
    with pytest.raises(ValueError, match="comment is required"):
        review.resolve(
            item["id"], None, "t", detail=_lines(item, **{"TC-1": {"decision": "reject"}})
        )
    with pytest.raises(ValueError, match="contradicts"):
        review.resolve(
            item["id"],
            "approve",
            "t",
            detail=_lines(item, **{"TC-1": {"decision": "reject", "comment": "x"}}),
        )
    with pytest.raises(ValueError, match="decide every line"):
        review.resolve(item["id"], "approve", "t")  # no blanket approve for line items
    assert review.resolve(item["id"], "defer", "t")["status"] == "resolved"


def test_invalid_and_double_decisions_rejected():
    _, item = _blocked_signoff()
    with pytest.raises(ValueError):
        review.resolve(item["id"], "maybe", "tester")
    review.resolve(item["id"], "defer", "tester")
    with pytest.raises(ValueError, match="already resolved"):
        review.resolve(item["id"], "defer", "tester")


def test_question_pick_needs_one_of_its_choices():
    run_id = runner.start_run(stub=True)
    ex_id = runner.get_run(run_id)["executions"][0]["id"]
    with pytest.raises(ValueError, match="two choices"):
        review.create(ex_id, "question", {"text": "cap?", "choices": [{"id": "A"}]})
    with pytest.raises(ValueError, match="cannot have lines"):
        review.create(ex_id, "question", {"lines": [{"id": "x"}], "choices": []})
    q = review.create(
        ex_id,
        "question",
        {
            "text": "limit cap?",
            "recommended": "B",
            "choices": [{"id": "A", "text": "no cap"}, {"id": "B", "text": "100"}],
        },
    )
    with pytest.raises(ValueError, match="detail.choice"):
        review.resolve(q, "pick", "t", detail={"choice": "Z"})
    assert review.resolve(q, "pick", "t", detail={"choice": "B"})["detail"] == {"choice": "B"}


def test_time_spent_is_logged_with_the_decision_not_on_the_item(data):
    import json

    from tp.core import runner

    run_id = runner.start_run("live", stub=True)
    ex = runner.rerun(run_id, "spec")["id"]
    item = review.create(ex, "factory_proposal", {})
    resolved = review.resolve(item, "accept", "po", seconds=12.5)
    assert "seconds" not in (resolved["detail"] or {})
    logged = json.loads((data / "decisions.jsonl").read_text().splitlines()[-1])
    assert logged["seconds"] == 12.5


def test_other_choice_needs_and_keeps_free_text():
    """M8: a question can be answered in the reviewer's own words."""
    from tp.core import runner

    run_id = runner.start_run(stub=True)
    ex = runner._latest(run_id, "spec")["id"]
    payload = {"choices": [{"id": "a", "text": "x"}, {"id": "other", "text": "Other"}],
               "recommended": "a"}  # fmt: skip
    i = review.create(ex, "question", payload)
    with pytest.raises(ValueError, match="needs detail.text"):
        review.resolve(i, "pick", "po", detail={"choice": "other"})
    item = review.resolve(i, "pick", "po", detail={"choice": "other", "text": "0 to 100 inclusive"})
    assert item["detail"] == {"choice": "other", "text": "0 to 100 inclusive"}


def test_a_cancelled_runs_items_stay_open_but_leave_the_active_list(data):
    run_id, item = _blocked_signoff()
    runner.cancel(run_id)
    assert [i["id"] for i in review.list_(status="open")] == [item["id"]]  # still resumable
    assert review.list_(status="open", active=True) == []
