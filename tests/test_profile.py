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
