"""pytest plugin (entry point `pytest11`): one recorder per test, a per-test timeout, the
origin of every failure, and one JSONL result record per test at $TP_RESULTS."""

import json
import os
import signal
import traceback

import pytest

from platform_rt import record
from platform_rt.api import api
from platform_rt.collect import KINDS, pytest_collect_file, suffix_of  # noqa: F401 (C3 hook)
from platform_rt.origin import TestTimeout, classify, declared

TIMEOUT = int(os.environ.get("TP_TIMEOUT", "60"))  # † seconds per test
_schema: dict | None = None


def _openapi() -> dict | None:
    global _schema
    if _schema is None:
        try:
            _schema = api.openapi()
        except Exception:  # noqa: BLE001 — no schema means no CONTRACT origin, not a crash
            _schema = {}
    return _schema


def _write(rec: dict) -> None:
    if path := os.environ.get("TP_RESULTS"):
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")


def _case_id(item) -> str:
    if cid := getattr(item, "tp_case_id", None):  # a collected file (C3) names its criterion
        return cid
    params = getattr(getattr(item, "callspec", None), "params", {})
    if "case_id" in params:
        return params["case_id"]
    marker = item.get_closest_marker("tp_case")
    return marker.kwargs.get("id", item.nodeid) if marker else item.nodeid


def _kind(item) -> str:
    """C1: case | generic | suite | ui. Collectors set `tp_kind`; the generic families are
    platform_rt's own test functions; any other test implements a signed case."""
    if kind := getattr(item, "tp_kind", None):
        return kind
    fn = getattr(item, "function", None)
    return "generic" if getattr(fn, "__module__", "").startswith("platform_rt.") else "case"


def pytest_configure(config):
    config.addinivalue_line("markers", "tp_case(id, unit): the case unit a test implements")
    if os.environ.get("MUTANT_UNDER_TEST") == "stats":
        _record_reached()


def _record_reached() -> None:
    """Gate linking (gate design D8): mutmut's trampoline calls `record_trampoline_hit` by its
    module-global name in stats mode; ours records the function on the current test instead
    of needing mutmut's project config."""
    try:
        from mutmut.mutation import trampoline
    except ImportError:
        return
    trampoline.record_trampoline_hit = lambda name, caller=None: record.current().reached.add(name)


def pytest_collection_modifyitems(items):
    """TP_SHUFFLE=<seed>: a reproducible random order (gate stability run, gate design D3)."""
    if seed := os.environ.get("TP_SHUFFLE"):
        import random

        random.Random(seed).shuffle(items)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item._tp = {"phases": {}}
    record.start(item.nodeid, _case_id(item))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    def alarm(signum, frame):
        raise TestTimeout(f"test exceeded {TIMEOUT}s")

    old = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(TIMEOUT)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    tp = getattr(item, "_tp", None)
    if tp is None:
        return
    rec = record.current()
    if rep.failed and call.excinfo is not None:
        exc = call.excinfo.value
        tb = traceback.extract_tb(call.excinfo.tb)
        origin = classify(call.when, exc, tb[-1].filename if tb else "", rec.exchanges,
                          rec.under_test, _openapi() if rec.exchanges else None,
                          rec.case_data)  # fmt: skip
        frame = next((f for f in reversed(tb) if f.filename == str(item.path)), None)
        steps = [e for e in rec.exchanges if not e["setup"]]
        tp["failure"] = tp.get("failure") or {
            "phase": call.when, "origin": origin, "error": f"{type(exc).__name__}: {exc}"[:2000],
            "line": frame.lineno if frame else None,
            "declared": declared(rec.under_test or (steps[-1] if steps else None),
                                 _openapi() if steps else None),
        }  # fmt: skip
    if rep.skipped and isinstance(rep.longrepr, tuple):  # why, for the report (checks/001 D9)
        tp["skip"] = rep.longrepr[2].removeprefix("Skipped: ")
    tp["phases"][call.when] = rep.outcome
    rep.tp_origin = tp.get("failure", {}).get("origin")
    if call.when == "teardown":
        failed = tp.get("failure")
        _write({
            "nodeid": item.nodeid, "case_id": rec.tc, "kind": _kind(item), "fault": rec.fault,
            "outcome": "failed" if failed else
                       ("skipped" if "skipped" in tp["phases"].values() else "passed"),
            "origin": failed and failed["origin"], "failure": failed,
            "assertions": rec.assertions, "observed": rec.observed,
            "under_test": rec.under_test and rec.under_test["step"],
            "exchanges": rec.exchanges, "trace_id": rec.trace_id, "reached": sorted(rec.reached),
            "retries": rec.retries, **({"skip": tp["skip"]} if "skip" in tp else {}),
        })  # fmt: skip


def pytest_collectreport(report):
    if report.failed:
        kind = KINDS.get(suffix_of(report.nodeid.split("::")[0]) or "", "case")
        _write({"nodeid": report.nodeid, "case_id": None, "kind": kind, "fault": None,
                "outcome": "failed", "origin": "BINDING",
                "failure": {"phase": "collect", "origin": "BINDING",
                            "error": str(report.longrepr)[-2000:], "line": None},
                "assertions": [], "observed": {}, "under_test": None, "exchanges": []})  # fmt: skip
