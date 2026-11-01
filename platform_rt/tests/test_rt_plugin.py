"""G1 + G3: a hand-written test produces a result record with assertion id and trace; each
origin is produced by a deliberately broken test."""

import json
import os
import textwrap
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = """
import pytest, httpx
from fastapi.testclient import TestClient
from rt_app import app
from platform_rt import api

class FactoryError(Exception):
    pass

@pytest.fixture(autouse=True)
def _client():
    api.use(TestClient(app, raise_server_exceptions=False))
    yield
    api.use(None)

@pytest.fixture
def broken_fixture():
    raise FactoryError("could not create user")

@pytest.fixture
def factory():
    def user():
        raise FactoryError("create user failed: 400")
    return user
"""

TESTS = """
import httpx
from platform_rt import api, expect, observe

def test_passes():
    api.get("/api/v1/items/", params={"limit": 2})
    expect.status(api.under_test({"limit": 2}), "2xx", id="TC-P/status")

def test_env():
    api.use(httpx.Client(base_url="http://127.0.0.1:9"))
    api.get("/api/v1/items/")

def test_data_in_setup(broken_fixture):
    pass

def test_data_in_call(factory):
    factory()

def test_binding():
    r = api.get("/api/v1/items/")
    observe("x", r.json()["no_such_field"])

def test_contract():
    r = api.get("/api/v1/broken-page/")
    observe("count", r.json()["count"])

def test_product_error():
    api.get("/api/v1/boom/")
    expect.status(api.under_test(), "2xx", id="TC-E/status")

def test_assertion():
    api.get("/api/v1/items/", params={"skip": 1})
    expect.status(api.under_test({"skip": 1}), 404, id="TC-A/status")

def test_conformance():
    api.get("/api/v1/items/", params={"skip": 0})
    expect.status(api.under_test({"skip": -1}), "4xx", id="TC-C/status")

def test_incomplete_request():
    api.post("/api/v1/items/", json={"description": "d"})
    expect.status(api.under_test({"description": "d"}), "2xx", id="TC-I/status")

def test_platform():
    expect.status({"no": "status"}, 200, id="TC-X/status")

def test_timeout():
    for _ in iter(int, 1):
        pass
"""


_cache: dict = {}


@pytest.fixture
def results(pytester, monkeypatch):
    if _cache:  # one subprocess run serves every test in this module
        return _cache
    here = Path(__file__).parent
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(here), str(here.parent)]))
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_broken=TESTS, test_syntax="def test_x(:\n    pass\n")
    out = pytester.path / "results.jsonl"
    monkeypatch.setenv("TP_RESULTS", str(out))
    monkeypatch.setenv("TP_TIMEOUT", "1")
    pytester.runpytest_subprocess(
        "-p", "platform_rt.plugin", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )
    _cache.update({r["nodeid"].split("::")[-1]: r
                   for r in map(json.loads, out.read_text().splitlines())})  # fmt: skip
    return _cache


def test_a_passing_test_has_a_record_with_assertion_id_and_trace(results):
    r = results["test_passes"]
    assert r["outcome"] == "passed" and r["origin"] is None
    assert r["assertions"] == [{"id": "TC-P/status", "ok": True, "expected": "2xx", "actual": 200}]
    assert r["exchanges"][0]["traceparent"].split("-")[1] == r["trace_id"]


@pytest.mark.parametrize(
    ("test", "origin"),
    [("test_env", "ENV"), ("test_data_in_setup", "DATA"), ("test_data_in_call", "DATA"),
     ("test_binding", "BINDING"), ("test_contract", "CONTRACT"),
     ("test_product_error", "PRODUCT_ERROR"), ("test_assertion", "ASSERTION"),
     ("test_conformance", "CONFORMANCE"), ("test_platform", "PLATFORM"),
     ("test_incomplete_request", "BINDING"),
     ("test_timeout", "TIMEOUT"), ("test_syntax.py", "BINDING")],
)  # fmt: skip
def test_each_origin_comes_from_a_broken_test(results, test, origin):
    r = results[test]
    assert (r["outcome"], r["origin"]) == ("failed", origin), textwrap.shorten(
        json.dumps(r["failure"]), 300
    )


def test_a_request_to_another_route_is_a_conformance_failure(pytester, monkeypatch):
    here = Path(__file__).parent
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(here), str(here.parent)]))
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(
        test_route="""
from platform_rt import api, expect

def test_wrong_route():
    api.post("/api/v1/items/", json={"title": None})
    expect.status(api.under_test({"title": None}, {"routes": ["PUT /api/v1/items/{id}"],
                                                    "auth": True}), 422, id="TC-R/status")
"""
    )
    out = pytester.path / "r.jsonl"
    monkeypatch.setenv("TP_RESULTS", str(out))
    pytester.runpytest_subprocess("-p", "platform_rt.plugin", "-p", "no:cacheprovider")
    (r,) = map(json.loads, out.read_text().splitlines())
    assert r["origin"] == "CONFORMANCE"  # the 422 from the wrong route does not count


def test_shuffle_is_reproducible_and_stats_mode_records_reached_functions(pytester, monkeypatch):
    here = Path(__file__).parent
    fake = pytester.path / "mutmut" / "mutation"  # a stand-in trampoline module
    fake.mkdir(parents=True)
    (pytester.path / "mutmut" / "__init__.py").write_text("")
    (fake / "__init__.py").write_text("")
    (fake / "trampoline.py").write_text("def record_trampoline_hit(name, caller=None):\n    raise "
                                        "RuntimeError('needs config')\n"
                                        "def call(name):\n    record_trampoline_hit(name)\n")  # fmt: skip
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(here),
                                                      str(here.parent)]))  # fmt: skip
    pytester.makepyfile(test_order="\n".join(
        f"def test_{i}():\n    from mutmut.mutation import trampoline\n"
        f"    trampoline.call('app.crud.f{i % 2}')\n" for i in range(8)))  # fmt: skip
    orders = []
    for seed in ("abc", "abc", "xyz"):
        out = pytester.path / f"o-{seed}-{len(orders)}.jsonl"
        monkeypatch.setenv("TP_RESULTS", str(out))
        monkeypatch.setenv("TP_SHUFFLE", seed)
        monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
        pytester.runpytest_subprocess("-p", "platform_rt.plugin", "-p", "no:cacheprovider")
        recs = [json.loads(ln) for ln in out.read_text().splitlines()]
        orders.append([r["nodeid"] for r in recs])
        assert all(r["outcome"] == "passed" for r in recs)
        assert {r["nodeid"].split("::")[1]: r["reached"] for r in recs}["test_3"] == ["app.crud.f1"]
    assert orders[0] == orders[1] != orders[2]
    assert orders[0] != sorted(orders[0], key=lambda n: int(n.rsplit("_", 1)[1]))
