from tp.core import artifacts, events, review, runner


def _approve_and_finish(run_id):
    item = review.list_(status="open")[-1]
    lines = {ln["id"]: {"decision": "approve"} for ln in item["payload"]["lines"]}
    review.resolve(item["id"], None, "tester", detail={"lines": lines})
    return runner.resume(item["id"])


def _statuses(run_id):
    run = runner.get_run(run_id)
    return {n: runner._latest(run_id, n)["status"] for n in runner.PIPELINE}, run


def test_stub_run_blocks_on_review_then_completes_with_lineage():
    run_id = runner.start_run(stub=True)
    run = runner.advance(run_id)
    assert run["status"] == "awaiting_review"
    run = _approve_and_finish(run_id)
    assert run["status"] == "succeeded"
    heads = runner.heads(run_id)
    assert len(heads) == 10  # nine stage outputs + the run's run_options input
    gate = artifacts.get(heads["gate_report"])
    assert sorted(gate.parents) == sorted([heads["bound_tests"], heads["cases"], heads["spec"]])
    assert gate.labels == ["simulated"]
    kinds = [e["kind"] for e in events.since(run_id)]
    assert "review_created" in kinds and kinds[-1] == "run_succeeded"


def test_superseding_cases_marks_exactly_its_consumers_stale():
    run_id = runner.start_run(stub=True, unattended=True)
    assert runner.advance(run_id)["status"] == "succeeded"
    fixture = artifacts.put("cases", {"hand": "written"}, labels=["pre-generated"])
    # Superseding the cases head: every consumer of cases (and theirs) goes stale.
    runner.rerun(run_id, "generate", {"cases": fixture.id})
    st, _ = _statuses(run_id)
    assert st["generate"] == "succeeded"
    assert {n for n, s in st.items() if s == "stale"} == {
        "gate",
        "execute",
        "triage",
        "report",
        "retest",
    }


def test_rerun_with_identical_output_marks_nothing_stale():
    run_id = runner.start_run(stub=True, unattended=True)
    runner.advance(run_id)
    runner.rerun(run_id, "gate")
    st, _ = _statuses(run_id)
    assert "stale" not in st.values()


def test_unattended_signoff_labels_unsigned_downstream():
    run_id = runner.start_run(stub=True, unattended=True)
    assert runner.advance(run_id)["status"] == "succeeded"
    assert "unsigned" in artifacts.get(runner.heads(run_id)["report"]).labels


def test_missing_input_fails_the_execution_not_the_runner():
    run_id = runner.start_run(stub=True)
    ex = runner.rerun(run_id, "gate")
    assert ex["status"] == "failed" and "missing inputs" in ex["reason"]


def test_from_run_reuses_another_runs_inputs():
    src = runner.start_run(stub=True, unattended=True)
    runner.advance(src)
    run_id = runner.start_run(stub=True)
    ex = runner.rerun(run_id, "gate", from_run=src)
    assert ex["status"] == "succeeded"
    assert ex["inputs"]["cases"] == runner.heads(src)["cases"]


def test_run_inputs_are_heads_from_the_start():
    req = artifacts.put("requirements", {"kind": "brief", "text": "test pagination"})
    run_id = runner.start_run("live", stub=True, inputs={"requirements": req.id})
    assert runner.heads(run_id)["requirements"] == req.id
    assert runner.get_run(run_id)["executions"][0]["stage"] == "input"


def test_unattended_never_confirms_observed_behaviour():
    assert review.UNATTENDED_DECISION["confirm_observed"] == "out_of_scope"


def test_resuming_a_stage_run_on_its_own_continues_only_that_stage():
    spec = artifacts.put("spec", {"stub": "spec"}, labels=["pre-generated"])
    run_id = runner.start_run("live", stub=True)
    ex = runner.rerun(run_id, "cases", overrides={"spec": spec.id})
    assert ex["status"] == "awaiting_review"
    item = review.list_(status="open")[-1]
    lines = {ln["id"]: {"decision": "approve"} for ln in item["payload"]["lines"]}
    review.resolve(item["id"], None, "tester", detail={"lines": lines})
    run = runner.resume(item["id"])
    statuses = {e["stage"]: e["status"] for e in run["executions"]}
    assert statuses["cases"] == "succeeded" and statuses["spec"] == "pending"


def test_the_llm_budget_covers_the_whole_execution_across_review_rounds(monkeypatch):
    from tp.core import contract, llm
    from tp.core.contract import StageResult

    calls = []

    def provider(prompt, model, params):
        calls.append(prompt)
        return "ok", {"input_tokens": 500_000}  # $2 a call at Opus prices

    class Spender:  # one call, a review, then two more calls after the review
        name, version, consumes, produces = "spec", "1", {}, {"spec": 1}

        def run(self, ctx, inputs):
            if not ctx.review.items():
                ctx.llm.call("round 1")
                return StageResult.awaiting_review([ctx.review.create("factory_proposal", {})])
            ctx.llm.call("round 2, first")
            ctx.llm.call("round 2, second")
            return StageResult.succeeded({"spec": {}})

    monkeypatch.setattr(llm, "anthropic_provider", provider)
    contract.load()  # register the real stages first, so they don't replace the test stage
    monkeypatch.setitem(contract.REGISTRY, "spec", Spender())
    run_id = runner.start_run("live", stub=False)
    runner.advance(run_id)
    item = review.list_(status="open")[-1]
    review.resolve(item["id"], "accept", "t")
    run = runner.resume(item["id"])
    spec = next(e for e in run["executions"] if e["stage"] == "spec")
    assert spec["status"] == "failed" and "BudgetExceeded" in spec["reason"]
    assert len(calls) == 2  # $2 + $2 > $3: the third call never went out
