"""External reach (docs/status/external/001): no management of the target, platform-side safe mode,
cleanup from the ledger, K and the ceiling. The target is a scripted transport."""

import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from tp.core import profile, secrets, strategies
from tp.stages.env import blackbox as bb

SPEC = {"openapi": "3.0.3", "paths": {
    "/api/items/": {"get": {}, "post": {}},
    "/api/items/{id}/": {"get": {}, "delete": {}},
}}  # fmt: skip


def _field(value):
    return {"source": "override", "evidence": None, "value": value}


RAW = {
    "schema_version": 1,
    "target": {"name": "live-thing", "repo": "", "type": "api"},
    "commit_tested": "unknown",
    "reach": _field({"strategy": "external", "url": "http://localhost:8010", "health": "/api/schema/"}),
    "spec": _field({"strategy": "served", "openapi": "/api/schema/", "base": "/api"}),
    "auth": _field({"strategy": "token_header", "scheme": "Token", "check": "/api/items/"}),
    "data": _field({"strategy": "spec_derived", "id_field": "id"}),
    "state": _field({"strategy": "namespace"}),
    "mutation": _field({"strategy": "none"}),
    "safety": _field({"strategy": "safe_mode"}),
    "checks": _field({"strategy": "pytest_cases"}),
}  # fmt: skip


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"subprocess: {a}"))
    sent = []

    def target(req: httpx.Request):
        sent.append((req.method, req.url.path))
        if req.url.path == "/api/schema/":
            return httpx.Response(200, json=SPEC)
        if req.method == "GET":
            return httpx.Response(200 if "authorization" in req.headers else 401, json={})
        if req.method == "DELETE":
            return httpx.Response(500 if req.url.path == "/api/items/3/" else 204)
        return httpx.Response(404)

    p = profile.parse(RAW, "live-thing")
    secrets.put(p.slug, "token", "s3cret-token")
    bb._STACKS.pop(p.slug, None)
    stack = bb.ExternalStack(p)
    stack.client = httpx.Client(
        base_url="http://localhost:8010", transport=httpx.MockTransport(target)
    )
    bb._STACKS[p.slug] = stack
    yield p, stack, sent
    bb._STACKS.pop(p.slug, None)


def test_external_reach_resolves_and_never_manages_the_target(live):
    p, stack, sent = live
    s = strategies.resolve(p)
    assert isinstance(s.reach, bb.ExternalReach) and isinstance(s.safety, bb.SafeMode)
    env = s.reach.provision("reference")  # subprocess.run fails the test if anything shells out
    assert (
        all(pr["ok"] for pr in env["probes"])
        and env["fingerprint"]["url"] == "http://localhost:8010"
        and {"run", "build"} <= set(env["fingerprint"]["network"])  # report renders both
    )
    s.reach.teardown("reference")
    assert s.reach.logs("reference") == "" and s.reach.exec(["id"]) is None
    with pytest.raises(bb.BlackboxError, match="doesn't manage"):
        stack.compose("down")
    with pytest.raises(bb.BlackboxError, match="redeploy the system yourself"):
        s.reach.provision("fixed:some.patch")
    assert {m for m, _ in sent} == {"GET"}


def test_the_platform_itself_only_reads_and_deletes_what_runs_created(live):
    _, stack, sent = live
    stack.openapi()
    for method, path in (("POST", "/api/items/"), ("DELETE", "/api/items/9/")):
        with pytest.raises(bb.BlackboxError, match="NOT_RUN: safe mode"):
            stack.http(method, path)
    stack.ledger.write_text("".join(json.dumps(e) + "\n" for e in [
        {"path": "/api/items/", "id": 1, "location": None},
        {"path": "/api/items/", "id": 2, "location": None},
        {"path": "/api/items/", "id": 2, "location": None, "deleted_by_test": True},
        {"path": "/api/items/", "id": 3, "location": None},  # the target fails this delete
    ]))  # fmt: skip
    assert stack.owned() == {"/api/items/1/", "/api/items/3/"}
    out = stack.cleanup(stack.ledger)
    assert [e["id"] for e in out["deleted"]] == [1] and [e["id"] for e in out["failed"]] == [3]
    assert ("DELETE", "/api/items/2/") not in sent  # a test already deleted it
    # the leftover stays in the ledger, so the next run (or `tp target clean`) retries it
    assert [json.loads(ln)["id"] for ln in stack.ledger.read_text().splitlines()] == [3]


def test_live_reach_lowers_k_and_rate_and_states_its_ceiling(live):
    from tp.stages.gate.stage import LIVE_K, settings
    from tp.stages.report.build import ceiling

    p, _, _ = live
    ctx = SimpleNamespace(strategies=strategies.resolve(p))
    assert settings(ctx, {})["k"] == LIVE_K == 3
    assert settings(ctx, {"gate": {"k": 5}})["k"] == 5  # an explicit choice still wins
    assert bb.families_config(p)["rate"] == 2
    names = {c: f.strategy for c, f in p.fields.items()}
    assert (
        ceiling(names, {"settings": {"k": 3}, "rows": []}) == "Seen on the live system, stable 3/3"
    )


def test_planned_writes_agree_with_the_proxy(monkeypatch):  # autonomy/001 F8
    """Sign-off's static list says what the proxy will do, for every write in the spec."""
    from platform_rt.proxy import Policy

    spec = {"paths": {
        "/api/items/": {"get": {}, "post": {}},
        "/api/items/{id}/": {"get": {}, "put": {}, "delete": {}},
        "/api/items/bulk_edit/": {"post": {}},
        "/api/users/{id}/": {"get": {}, "delete": {}},
        "/api/notes/": {"post": {}},
        "/api/tasks/": {"post": {"x-required-scope": ["admin"]}},
        "/api/tasks/{id}/": {"get": {}, "delete": {}},
    }}  # fmt: skip
    deny = ["user", "bulk", "admin"]
    monkeypatch.setattr(bb, "safety_config", lambda: {"deny": deny})
    plan = bb.SafeMode(None).plan(spec)
    policy = Policy(spec, deny, "r")
    for p in plan:
        m, tpl = p["op"].split(" ", 1)
        ok, rule = policy.decide(m, tpl.replace("{id}", "7"), json.dumps({"t": "tp-r-x"}).encode())
        if p["verdict"] == "create":
            assert ok and rule == "create", p
        elif p["verdict"] == "own_items":
            assert not ok and rule == "not created by this run", p  # allowed once the run made it
        else:
            assert not ok and rule == p["rule"], p
    assert {p["op"]: p["verdict"] for p in plan} == {
        "POST /api/items/": "create", "PUT /api/items/{id}/": "own_items",
        "DELETE /api/items/{id}/": "own_items", "POST /api/items/bulk_edit/": "blocked",
        "DELETE /api/users/{id}/": "blocked", "POST /api/notes/": "blocked",
        "POST /api/tasks/": "blocked", "DELETE /api/tasks/{id}/": "own_items"}  # fmt: skip
