import json

import pytest

from tp.core import artifacts, llm, runner


def test_replay_is_byte_identical_with_no_provider():
    recorded = llm.Gateway("record", lambda p, m, k: "héllo\r\n" + p).call("hi", temperature=0)
    replayed = llm.Gateway("replay").call("hi", temperature=0)
    assert replayed.encode() == recorded.encode()


def test_replay_miss_never_goes_live():
    with pytest.raises(llm.ReplayMiss):
        llm.Gateway("replay").call("never recorded")


def test_replayed_stub_run_matches_and_is_labelled_recorded(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    live = runner.start_run(mode="record", stub=True, unattended=True)
    runner.advance(live)
    replay = runner.start_run(mode="replay", stub=True, unattended=True)
    assert runner.advance(replay)["status"] == "succeeded"
    a, b = (artifacts.get(runner.heads(r)["spec"]) for r in (live, replay))
    assert a.body == b.body
    assert "recorded" in b.labels and "recorded" not in a.labels


def test_replay_without_recording_fails_the_stage():
    run_id = runner.start_run(mode="replay", stub=True)
    run = runner.advance(run_id)
    assert run["status"] == "failed"
    assert "ReplayMiss" in run["executions"][0]["reason"]


def test_usage_is_priced_and_logged_and_budget_stops_live_calls(data):
    calls = []

    def provider(prompt, model, params):
        calls.append(prompt)
        return "ok", {"input_tokens": 500_000, "output_tokens": 50_000}  # $2 + $1 on Opus

    gw = llm.Gateway("record", provider, budget_usd=3.0)
    gw.call("first")
    assert gw.spent_usd == pytest.approx(3.0)
    with pytest.raises(llm.BudgetExceeded):
        gw.call("second")
    assert calls == ["first"]  # the over-budget call never reached the provider
    cached = [json.loads(p.read_text()) for p in (data / "llm_cache").iterdir()]
    assert cached[0]["cost_usd"] == pytest.approx(3.0)


def test_replay_costs_nothing():
    llm.Gateway("record", lambda p, m, k: ("x", {"input_tokens": 10**6})).call("q")
    gw = llm.Gateway("replay", budget_usd=0.0)
    assert gw.call("q") == "x" and gw.spent_usd == 0.0


def test_haiku_is_priced_lower_than_opus():
    usage = {"input_tokens": 10**6, "output_tokens": 10**6}
    assert llm.cost_usd("claude-haiku-5-5", usage) < llm.cost_usd("claude-opus-5-5", usage)
