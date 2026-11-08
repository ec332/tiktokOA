"""W0 contract C6: discovery calls its iteration 3 hooks in a fixed order, and the hook stubs
leave the profile exactly as before (docs/iteration3/W0-contracts.md)."""

from tp.stages.discover import dependencies, discovery, spec_base, ui_flows

ORDER = ["reach", "spec", "auth", "data", "secured", "spec_check", "cleanup", "dependencies",
         "ui", "resolver"]  # fmt: skip


def test_steps_run_in_the_contract_order_with_ui_after_dependencies(monkeypatch):
    called = []
    for name in ORDER:
        monkeypatch.setattr(discovery.Discovery, f"step_{name}",
                            lambda self, n=name: called.append(n))  # fmt: skip
    monkeypatch.setattr(discovery.Discovery, "profile", lambda self: {})
    discovery.Discovery.__new__(discovery.Discovery).run()
    assert called == ORDER


def test_the_hook_steps_delegate_to_their_modules(monkeypatch):
    seen = []
    monkeypatch.setattr(dependencies, "run", lambda d: seen.append(("dependencies", d)))
    monkeypatch.setattr(ui_flows, "run", lambda d: seen.append(("ui", d)))
    d = discovery.Discovery.__new__(discovery.Discovery)
    d.step_dependencies()
    d.step_ui()
    assert seen == [("dependencies", d), ("ui", d)]


def test_the_stubs_change_nothing_until_p4_and_p7_fill_them():
    assert spec_base.base({"paths": {}, "servers": [{"url": "/api"}]}, "/v1") == "/v1"
    assert spec_base.paging_filter(["offset", "limit", "page"]) == ["offset", "limit", "page"]
    assert ui_flows.run(object()) is None
