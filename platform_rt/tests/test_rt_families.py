"""Generic families (checks/001): the same code on two conventions, against a scripted fake. One
shape is Paperless-like (integer IDs, page/page_size, a secured spec), the other Memos-like
(resource names, pageSize/pageToken, a spec that declares no security, D10). A broken fake shows
that the claims fail with the right origin: PRODUCT_* for defects, SPEC for spec drift."""

import itertools
import json
from pathlib import Path

import httpx
import pytest
from platform_rt.expect import SpecMismatch
from platform_rt.origin import classify

from platform_rt import api, families, record
from tp.core import profile
from tp.stages.env.blackbox import families_config

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "targets"
ERR = {"description": "error"}
SHAPES = {
    "paperless-ngx": {"coll": "/api/tags/", "item": "/api/tags/{id}/", "param": ("id", "integer"),
                      "field": "name", "max": 128, "secured": True, "items": "results",
                      "paging": [("page", "integer"), ("page_size", "integer")], "created": 201},
    "memos": {"coll": "/api/v1/memos", "item": "/api/v1/memos/{memo}", "param": ("memo", "string"),
              "field": "content", "max": None, "secured": False, "items": "memos",
              "paging": [("pageSize", "integer"), ("pageToken", "string")], "created": 200},
}  # fmt: skip


def openapi(s: dict) -> dict:
    field = {"type": "string"} | ({"maxLength": s["max"]} if s["max"] else {})
    body = {"type": "object", "required": [s["field"]], "properties": {s["field"]: field}}
    page = {
        "type": "object",
        "required": [s["items"]],
        "properties": {s["items"]: {"type": "array"}},
    }
    q = [{"name": n, "in": "query", "schema": {"type": t}} for n, t in s["paging"]]
    p = [{"name": s["param"][0], "in": "path", "required": True, "schema": {"type": s["param"][1]}}]
    resp = lambda ok: {ok: {"description": "ok"}, **{c: ERR for c in ("400", "401", "404")}}
    return {"openapi": "3.0.3", "info": {"title": "fake", "version": "1"},
            **({"security": [{"tokenAuth": []}], "components": {"securitySchemes": {
                "tokenAuth": {"type": "apiKey", "in": "header", "name": "Authorization"}}}}
               if s["secured"] else {}),
            "paths": {
                s["coll"]: {
                    "get": {"parameters": q, "responses": {"200": {"description": "page", "content": {
                        "application/json": {"schema": page}}}} | {c: ERR for c in ("400", "401", "404")}},
                    "post": {"requestBody": {"content": {"application/json": {"schema": body}}},
                             "responses": resp(str(s["created"]))}},
                s["item"]: {"get": {"parameters": p, "responses": resp("200")},
                            "delete": {"parameters": p, "responses": resp("204")}}}}  # fmt: skip


def fake(s: dict, bug: str | None = None):
    store, ids = {}, itertools.count(1)

    def handler(req: httpx.Request) -> httpx.Response:
        authed = req.headers.get("authorization") == "Token good"
        path, stem = req.url.path, s["item"].rsplit("/{", 1)[0]
        if not authed and (s["secured"] or req.method != "GET"):
            return httpx.Response(401, json={"detail": "auth"})
        if path == s["coll"] and req.method == "GET":
            q = dict(req.url.params)
            try:
                sizes = [int(q[n]) for n, t in s["paging"] if t == "integer" and n in q]
            except ValueError:
                return httpx.Response(400, json={})
            if any(v < 0 for v in sizes) or q.get("pageToken", "") not in ("", "p1"):
                return httpx.Response(400, json={})
            if "page" in q and not 1 <= int(q["page"]) <= 1:
                return httpx.Response(404, json={"detail": "Invalid page."})
            return httpx.Response(200, json={s["items"]: list(store.values())})
        if path == s["coll"] and req.method == "POST":
            try:
                body = __import__("json").loads(req.content or b"null")
            except ValueError:
                return httpx.Response(400, json={})
            v = body.get(s["field"]) if isinstance(body, dict) else None
            if not isinstance(v, str) or (s["max"] and len(v) > s["max"]):
                return httpx.Response(400, json={})
            n = next(ids)
            key = str(n) if s["param"][1] == "integer" else f"m{n:x}"
            obj = {s["field"]: v} | (
                {"id": n} if s["param"][1] == "integer" else {"name": f"memos/{key}"}
            )
            store[key] = obj
            return httpx.Response(s["created"], json=obj)
        if path.startswith(stem + "/"):
            key = path[len(stem) + 1 :].rstrip("/")
            if key not in store:
                return httpx.Response(500 if bug == "unknown_id_500" else 404, json={})
            if req.method == "DELETE":
                if bug != "soft_delete":
                    del store[key]
                return httpx.Response(204)
            return httpx.Response(200, json=store[key])
        return httpx.Response(404, json={})

    return handler


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TP_SECRET_TOKEN", "good")
    monkeypatch.setenv("TP_AUTH_SCHEME", "Token")
    monkeypatch.delenv("TP_LEDGER", raising=False)
    yield
    api.use(None)


def conf(slug: str, monkeypatch) -> dict:
    monkeypatch.setenv("TP_TARGET", str(FIXTURES / slug))
    return families.DEFAULTS | families_config(profile.load())


def run_all(slug: str, monkeypatch, bug: str | None = None) -> dict[str, str]:
    """case id -> passed | skip:<reason> | <origin> of the failure."""
    s, c = SHAPES[slug], conf(slug, monkeypatch)
    spec = openapi(s)
    families._st_schema = None
    api.use(httpx.Client(transport=httpx.MockTransport(fake(s, bug)), base_url="http://t"))
    out = {}
    for cid, why in families.plan(spec, c):
        assert why is None
        rec = record.start(f"test_tp_families.py::test_generic[{cid}]", cid)
        try:
            families.run(cid, spec, c)
            out[cid] = "passed"
        except pytest.skip.Exception as e:
            out[cid] = f"skip:{str(e).split(':')[0]}"
        except AssertionError as e:
            out[cid] = classify("call", e, "", rec.exchanges, rec.under_test, spec)
    return out


@pytest.mark.parametrize("slug", ["paperless-ngx", "memos"])
def test_the_same_families_run_on_both_conventions(slug, monkeypatch):
    got = run_all(slug, monkeypatch)
    s = SHAPES[slug]
    assert {cid.split(":")[1] for cid in got} == set(families.FAMILIES)  # every family has a case
    assert got[f"G:read_back:POST {s['coll']}"] == "passed"  # resource IDs and names both resolve
    assert got[f"G:delete_final:POST {s['coll']}"] == "passed"
    assert got[f"G:paging:GET {s['coll']}"] == "passed"  # page/page_size and pageSize/pageToken
    assert got[f"G:unknown_id:GET {s['item']}"] == "passed"
    assert got[f"G:invalid_input:POST {s['coll']}"] == "passed"
    assert all(v == "passed" for k, v in got.items() if k.startswith("G:properties:"))
    # anonymous reads: refused on the secured spec; allowed by the unsecured one, so server and
    # spec disagree (D5): SPEC, never a defect
    anon_list = got[f"G:auth_required:GET {s['coll']}"]
    assert anon_list == ("passed" if s["secured"] else "SPEC")
    assert "PRODUCT_ERROR" not in got.values() and "ASSERTION" not in got.values()


def test_defects_fail_as_product_and_drift_as_spec(monkeypatch):
    got = run_all("paperless-ngx", monkeypatch, bug="unknown_id_500")
    assert got["G:unknown_id:GET /api/tags/{id}/"] == "PRODUCT_ERROR"
    assert got["G:properties:GET /api/tags/{id}/"] == "PRODUCT_ERROR"  # Schemathesis: ServerError
    got = run_all("paperless-ngx", monkeypatch, bug="soft_delete")
    assert got["G:delete_final:POST /api/tags/"] == "ASSERTION"


def test_an_undeclared_status_is_spec_drift(monkeypatch):
    s = SHAPES["paperless-ngx"]
    spec = openapi(s)
    del spec["paths"][s["item"]]["get"]["responses"]["404"]  # the spec forgets its 404
    c = conf("paperless-ngx", monkeypatch)
    families._st_schema = None
    api.use(httpx.Client(transport=httpx.MockTransport(fake(s)), base_url="http://t"))
    rec = record.start("t::x", "G:properties:GET /api/tags/{id}/")
    with pytest.raises(SpecMismatch) as e:
        families.run("G:properties:GET /api/tags/{id}/", spec, c)
    assert classify("call", e.value, "", rec.exchanges, rec.under_test, spec) == "SPEC"
    assert "Undocumented status code" in rec.observed["checks"]
    assert (
        "Authorization: [Filtered]" in rec.observed["curl"] and "good" not in rec.observed["curl"]
    )


def test_exclusions_from_the_profile_become_skips(monkeypatch):
    s = SHAPES["paperless-ngx"]
    c = conf("paperless-ngx", monkeypatch) | {
        "exclude": [{"op": f"POST {s['coll']}", "reason": "needs_dependency"}]
    }
    planned = dict(families.plan(openapi(s), c))
    assert planned[f"G:read_back:POST {s['coll']}"] == "needs_dependency"
    assert planned[f"G:unknown_id:GET {s['item']}"] is None


def test_a_masked_secret_is_not_a_round_trip_failure(monkeypatch):
    """r-0014: Paperless reads a mail account's password back as `****`; not a defect."""
    s = dict(SHAPES["paperless-ngx"])
    spec = openapi(s)
    inner = fake(s)

    def masking(req):
        r = inner(req)
        if req.method == "GET" and r.status_code == 200 and "name" in (body := r.json()):
            return httpx.Response(200, json=body | {"name": "*" * 12})
        return r

    c = conf("paperless-ngx", monkeypatch)
    api.use(httpx.Client(transport=httpx.MockTransport(masking), base_url="http://t"))
    record.start("t::x", "G:read_back:POST /api/tags/")
    families.run("G:read_back:POST /api/tags/", spec, c)  # passes: the field is masked


def test_a_server_error_is_not_hidden_behind_earlier_spec_drift(monkeypatch):
    """checks/002: an undocumented 404 in one example must not stop the examples; a later 500
    is still found, as a product error."""
    s = SHAPES["paperless-ngx"]
    spec = openapi(s)
    del spec["paths"][s["item"]]["get"]["responses"]["404"]
    seen = []

    def handler(req):
        if (
            req.method == "GET"
            and req.url.path.startswith("/api/tags/")
            and req.url.path != "/api/tags/"
        ):
            key = req.url.path.rstrip("/").rsplit("/", 1)[1]
            seen.append(key)
            even = key.lstrip("-").isdigit() and not int(key) % 2
            return httpx.Response(500 if even else 404, json={})
        return fake(s)(req)

    c = conf("paperless-ngx", monkeypatch)
    families._st_schema = None
    api.use(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t"))
    rec = record.start("t::x", "G:properties:GET /api/tags/{id}/")
    with pytest.raises(AssertionError) as e:
        families.run("G:properties:GET /api/tags/{id}/", spec, c)
    assert int(seen[0]) % 2  # the first example drifted (an undocumented 404) ...
    assert classify("call", e.value, "", rec.exchanges, rec.under_test, spec) == "PRODUCT_ERROR"


def test_the_runs_own_routes_are_sampled_first(monkeypatch):
    s = SHAPES["paperless-ngx"]
    c = conf("paperless-ngx", monkeypatch) | {"per_family": 1}
    plain = [cid for cid, _ in families.plan(openapi(s), c) if ":properties:" in cid]
    focused = [
        cid
        for cid, _ in families.plan(openapi(s), c, [f"GET {s['item']}"])
        if ":properties:" in cid
    ]
    assert plain != focused == [f"G:properties:GET {s['item']}"]


def test_generate_embeds_the_runs_routes():
    from tp.stages.generate.stage import families_entry

    src = families_entry(["POST /api/tags/", "GET /api/tags/"])["source"]
    line = next(x for x in src.splitlines() if x.startswith("TP_FOCUS = "))
    assert json.loads(line.removeprefix("TP_FOCUS = ").split("  #")[0]) == [
        "GET /api/tags/",
        "POST /api/tags/",
    ]
