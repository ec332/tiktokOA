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
