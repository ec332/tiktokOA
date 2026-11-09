"""W0 contract C4: the fixed BOUND entries come from ENTRY_PROVIDERS, in order, and the runner
image follows the entries' kinds (docs/iteration3/W0-contracts.md)."""

from types import SimpleNamespace

from tp.stages.generate import image, stage, suite, ui


def _ctx(own_image: str | None):
    reach = SimpleNamespace(image=lambda variant: own_image)
    return SimpleNamespace(strategies=SimpleNamespace(reach=reach), profile=None)


def test_providers_are_the_families_then_suites_then_ui_flows():
    assert stage.ENTRY_PROVIDERS == (stage._families, suite.entries, ui.entries)
    assert suite.entries(_ctx(None), {}) == [] and ui.entries(_ctx(None), {}) == []  # P5, P7


def test_the_families_entry_is_black_box_only_and_marked_generic():
    (entry,) = stage._families(_ctx(None), {"routes": ["GET /x"]})
    assert (entry["kind"], entry["generic"], entry["status"]) == ("generic", True, "BOUND")
    assert stage._families(_ctx("tp-template:abc"), {}) == []  # the template has its own image
    assert stage._families(_ctx(None), {"units": ["U1"]}) == []  # a run limited to some units


def test_a_ui_entry_selects_the_browser_runner(monkeypatch):
    monkeypatch.setattr(image, "published_browser", lambda: {"image": "browser"})
    monkeypatch.setattr(image, "published_blackbox", lambda: {"image": "blackbox"})
    monkeypatch.setattr(image, "published", lambda: {"image": "template"})
    cases = [{"kind": "case"}, {"kind": "generic"}]
    assert stage._runner(_ctx(None), cases)["image"] == "blackbox"
    assert stage._runner(_ctx("tp-template:abc"), cases)["image"] == "template"
    assert stage._runner(_ctx(None), [*cases, {"kind": "ui"}])["image"] == "browser"
