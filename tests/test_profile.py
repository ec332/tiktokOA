"""Target profile, reverse shim and resolver (seams/001 A3-A4)."""

import pytest

from tp.core import profile, strategies, sut


def test_template_profile_loads_and_every_field_is_an_override():
    p = profile.load()
    assert p.slug == "fastapi-template" and len(p.commit_tested) == 40
    assert {c: p.fields[c].source for c in profile.CONCERNS} == dict.fromkeys(
        profile.CONCERNS, "override"
    )


def test_reverse_shim_gives_the_iteration_1_config_keys():
    cfg = profile.legacy(profile.load())
    assert set(sut.REQUIRED) <= set(cfg)
    assert cfg["ref"] == profile.load().commit_tested
    assert cfg["patches"] == ["testinfo", "flags", "trace"]


def test_parse_rejects_a_field_without_strategy_or_source():
    raw = {"target": {"name": "x", "repo": "y"}, "commit_tested": "z"}
    raw |= {c: {"source": "override", "value": {"strategy": "s"}} for c in profile.CONCERNS}
    profile.parse(raw, "x")
    with pytest.raises(profile.ProfileError, match="reach"):
        profile.parse(raw | {"reach": {"source": "guessed", "value": {"strategy": "s"}}}, "x")
    with pytest.raises(profile.ProfileError, match="spec"):
        profile.parse(raw | {"spec": {"source": "override", "value": {}}}, "x")


def test_resolver_is_a_function_of_the_profile():
    p = profile.load()
    a, b = strategies.resolve(p), strategies.resolve(p)
    assert {c: type(getattr(a, c)).__name__ for c in profile.CONCERNS} == {
        "reach": "ContainerReach", "spec": "ServedSpec", "auth": "OverrideAuth",
        "data": "OverrideData", "state": "TransactionState", "mutation": "InProcessMutation",
        "safety": "Unrestricted", "checks": "PytestCases",
    }  # fmt: skip
    assert all(type(getattr(a, c)) is type(getattr(b, c)) for c in profile.CONCERNS)


def test_resolver_names_a_missing_strategy():
    p = profile.load()
    fields = dict(p.fields) | {"reach": profile.Field({"strategy": "teleport"}, "override")}
    bad = profile.TargetProfile(p.slug, p.name, p.repo, p.type, p.commit_tested, fields)
    with pytest.raises(strategies.ResolveError, match="teleport"):
        strategies.resolve(bad)


# ---- discover/006: choosing a target per run --------------------------------------------------


@pytest.fixture
def discovered(tmp_path, monkeypatch):
    """A discovered profile under <data>/targets/memos (what `tp target add` writes)."""
    import shutil
    from pathlib import Path

    monkeypatch.setenv("TP_DATA", str(tmp_path))
    monkeypatch.delenv("TP_TARGET", raising=False)
    dest = tmp_path / "targets" / "memos"
    shutil.copytree(Path("tests/fixtures/targets/memos"), dest)
    return dest


def test_a_bare_slug_finds_a_discovered_profile(discovered, monkeypatch):
    monkeypatch.setenv("TP_TARGET", "memos")
    assert profile.target_dir() == discovered
    assert profile.load().slug == "memos"


def test_selected_switches_the_target_for_a_block_and_restores_it(discovered):
    with profile.selected(str(discovered)):
        assert profile.load().slug == "memos"
    assert profile.load().slug == "fastapi-template"  # back to the default
    with profile.selected(None):
        assert profile.load().slug == "fastapi-template"


def test_choices_list_hand_written_and_discovered_targets(discovered):
    got = {(c["slug"], c["kind"], c["black_box"]) for c in profile.choices()}
    assert got == {("fastapi-template", "hand_written", False), ("memos", "discovered", True)}


def test_a_run_is_pinned_to_its_target_for_every_stage(discovered, monkeypatch):
    from tp.core import runner

    rid = runner.start_run("replay", stub=True, options={"profile": "memos"})
    assert runner.run_target(rid) == str(discovered.resolve())
    seen = []
    monkeypatch.setattr(runner, "_execute_one", lambda *a: seen.append(profile.load().slug))
    runner._execute(rid, 0)
    assert seen == ["memos"] and profile.load().slug == "fastapi-template"
    other = runner.start_run("replay", stub=True)  # no choice: the default, recorded
    assert runner.run_target(other).endswith("targets/fastapi-template")
    assert runner.run_target(runner.start_run("replay", stub=True, pipeline=["discover"])) is None
