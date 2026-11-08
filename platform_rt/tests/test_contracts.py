"""W0 contracts C1 and C3 (docs/iteration3/W0-contracts.md): a test file in another format is
collected by suffix and writes the same per-test record as a pytest test; a format with no runner
fails collection instead of passing; `kind`, `fault` and the two UI origins are recorded."""

import json
import os
from pathlib import Path

import pytest

from platform_rt import origin

pytest_plugins = ["pytester"]

# A collector for a made-up suffix, registered the way P5 and P7 register theirs (COLLECTORS).
DUMMY = """
import pytest
from platform_rt import record
from platform_rt.expect import ExpectationFailed
from platform_rt.origin import LocatorError, UiPageError


class Step(pytest.Item):
    def runtest(self):
        rec = record.current()
        rec.assertions.append({"id": self.name, "ok": self.how == "ok", "expected": 1, "actual": 1})
        if self.how == "fault":
            rec.fault = {"standin": "hook", "mode": "status", "value": 500}
        if self.how == "bad":
            raise ExpectationFailed(self.name, "expected 1, got 2")
        if self.how == "page":
            raise UiPageError("TypeError in page script")
        if self.how == "locator":
            raise LocatorError("no button named Save")


class Flow(pytest.File):
    def collect(self):
        for how in ("ok", "bad", "page", "locator", "fault"):
            item = Step.from_parent(self, name=f"{how}[U:demo-{how}]")
            item.how, item.tp_case_id, item.tp_kind = how, f"U:demo-{how}", "ui"
            yield item


def collect(file_path, parent):
    return Flow.from_parent(parent, path=file_path)
"""
CONFTEST = (
    'from platform_rt import collect\ncollect.COLLECTORS[".tpdummy"] = "dummy_collector:collect"\n'
)

_cache: dict = {}


@pytest.fixture
def records(pytester, monkeypatch):
    if _cache:
        return _cache
    here = Path(__file__).parent
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(here.parent)]))
    pytester.makepyfile(dummy_collector=DUMMY, test_plain="def test_plain():\n    pass\n")
    pytester.makeconftest(CONFTEST)
    (pytester.path / "demo.tpdummy").write_text("flow: demo\n")
    (pytester.path / "auth.hurl").write_text("GET {{host}}/api/user\nHTTP 401\n")
    out = pytester.path / "results.jsonl"
    monkeypatch.setenv("TP_RESULTS", str(out))
    pytester.runpytest_subprocess(
        "-p", "platform_rt.plugin", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )
    _cache.update({r["nodeid"]: r for r in map(json.loads, out.read_text().splitlines())})
    return _cache


def test_a_collected_format_writes_the_same_record_as_pytest(records):
    plain = records["test_plain.py::test_plain"]
    ok = records["demo.tpdummy::ok[U:demo-ok]"]
    assert set(ok) == set(plain)  # same schema, so gate, triage and retest need no change
    assert (ok["outcome"], ok["case_id"], ok["kind"]) == ("passed", "U:demo-ok", "ui")
    assert (plain["kind"], plain["fault"]) == ("case", None)


@pytest.mark.parametrize(
    ("node", "origin_"),
    [("bad", "ASSERTION"), ("page", "UI_ERROR"), ("locator", "BINDING")],
)
def test_each_failure_of_a_collected_item_has_its_origin(records, node, origin_):
    r = records[f"demo.tpdummy::{node}[U:demo-{node}]"]
    assert (r["outcome"], r["origin"]) == ("failed", origin_)


def test_an_injected_fault_is_on_the_record(records):
    r = records["demo.tpdummy::fault[U:demo-fault]"]
    assert r["fault"] == {"standin": "hook", "mode": "status", "value": 500}


def test_a_format_with_no_runner_fails_collection_and_never_passes(records):
    r = records["auth.hurl"]
    assert (r["outcome"], r["origin"], r["kind"]) == ("failed", "BINDING", "suite")
    assert "no runner for auth.hurl" in r["failure"]["error"]


def test_ui_errors_raised_inside_platform_rt_are_not_platform_errors():
    inside = origin.HERE + "/ui.py"
    assert origin.classify("call", origin.UiPageError("x"), inside, [], None) == "UI_ERROR"
    assert origin.classify("call", origin.LocatorError("x"), inside, [], None) == "BINDING"
    assert origin.classify("call", RuntimeError("x"), inside, [], None) == "PLATFORM"
    # what a Hurl or UI item raises for a failed assert (C3): an assertion, not a platform error
    failed = origin.ExpectationFailed("S:pack:file", "expected 201, got 200")
    assert origin.classify("call", failed, inside, [], None) == "ASSERTION"
