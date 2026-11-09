"""W0 contract C6: discovery calls its iteration 3 hooks in a fixed order, and the hook stubs
leave the profile exactly as before (docs/iteration3/W0-contracts.md)."""

from types import SimpleNamespace

from tp.stages.discover import dependencies, discovery, ui_flows

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


def test_the_ui_hook_proposes_nothing_without_a_model():
    # P4 and P7 filled the hooks (domains/002 R4, browser/002 R4); their own tests cover them
    d = SimpleNamespace(llm=None)
    assert ui_flows.run(d) is None and not hasattr(d, "ui_flows")
