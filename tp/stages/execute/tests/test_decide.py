from tp.stages.execute import decide


def ran(outcome="passed", origin=None, failed=()):
    return {"status": "RAN", "outcome": outcome, "origin": origin,
            "assertions": [{"id": a, "ok": False} for a in failed]}  # fmt: skip


def test_pass_needs_every_attempt_to_pass():
    assert decide.verdict([ran()] * 3) == {"verdict": "PASS", "dist": "0/3"}


def test_consistent_fail_needs_same_origin_and_assertion():
    f = ran("failed", "PRODUCT_ERROR", ["TC-1/status"])
    v = decide.verdict([f] * 3)
    assert v["verdict"] == "CONSISTENT_FAIL" and v["dist"] == "3/3"
    assert v["origin"] == "PRODUCT_ERROR" and v["assertions"] == ["TC-1/status"]


def test_mixed_is_intermittent_including_three_different_failures():
    f1 = ran("failed", "ASSERTION", ["TC-1/status"])
    f2 = ran("failed", "ASSERTION", ["TC-1/body"])
    assert decide.verdict([ran(), f1, f1])["verdict"] == "INTERMITTENT"
    assert decide.verdict([f1, f2, f1])["verdict"] == "INTERMITTENT"


def test_timeout_and_env_states_give_no_product_verdict():
    t = ran("failed", "TIMEOUT")
    assert decide.verdict([t] * 3)["verdict"] == "TIMEOUT"
    assert decide.verdict([ran(), {"status": "SUSPECT_ENV"}, ran()])["verdict"] == "SUSPECT_ENV"
    v = decide.verdict([ran(), {"status": "NOT_RUN", "reason": "ENV: pre-probe failed"}, ran()])
    assert v["verdict"] == "NOT_RUN" and v["reason"] == "ENV: pre-probe failed"


def test_escalated_only_when_stable_on_reference():
    fails = [ran("failed")] * 3 + [ran()] * 7
    assert decide.escalation(fails, [ran()] * 10)["escalated"] is True
    assert decide.escalation(fails, [ran(), ran("failed")])["escalated"] is False
    assert decide.escalation(fails, [])["escalated"] is False  # no reference evidence
