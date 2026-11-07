"""Generic check families (checks/001 D1–D5, D11): fixed platform code, no LLM. They read the
target's served OpenAPI and its profile's conventions ($TP_FAMILIES: ID field and style, paging
style, exclusions), so the same code checks integer-ID/page targets and resource-name/token ones.

Every claim needs no requirement (D4): never 5xx, never 2xx without credentials on a secured
operation, never 2xx for an unknown ID or after a delete, declared fields round-trip. When the
server and its own spec disagree, the failure is SpecMismatch (origin SPEC), never a defect."""

import json
import os
import random
import re

import pytest

from platform_rt import record
from platform_rt.api import api
from platform_rt.api import token as api_token
from platform_rt.blackbox import eventually
from platform_rt.expect import ExpectationFailed, SpecMismatch
from platform_rt.origin import resolve

FAMILIES = ("properties", "auth_required", "unknown_id", "invalid_input", "read_back",
            "delete_final", "paging")  # build order = cut order (D15)  # fmt: skip
DEFAULTS = {"id_style": "integer", "id_field": "id", "paging": None, "exclude": [],
            "per_family": 5, "examples": 10}  # fmt: skip
METHODS = ("get", "post", "put", "patch", "delete")
PARAM = re.compile(r"\{[^}]+\}")
WRONG = {"string": {"tp": "not a string"}, "integer": "not-a-number", "number": "not-a-number",
         "boolean": "not-a-boolean", "array": "not-an-array", "object": "not-an-object"}  # fmt: skip
PAGING = {"page": [("page", 0), ("page", -1), ("page", 10**6), ("size", 0), ("size", -1),
                   ("size", 10**6)],
          "token": [("token", "tp-bad-token"), ("size", 0), ("size", -1), ("size", 10**6)]}  # fmt: skip


def conf() -> dict:
    return DEFAULTS | json.loads(os.environ.get("TP_FAMILIES") or "{}")


# ---- reading the spec ------------------------------------------------------------------


def _ops(spec: dict) -> list[tuple[str, str, dict]]:
    return [(m.upper(), p, op | {"_shared": item.get("parameters", [])})
            for p, item in spec.get("paths", {}).items() for m, op in item.items() if m in METHODS]  # fmt: skip


def resources(spec: dict) -> list[dict]:
    """Collections with a create and an item GET one segment below; full CRUD first (D11)."""
    paths, out = spec.get("paths", {}), []
    for p, item in paths.items():
        if PARAM.search(p) or "post" not in item:
            continue
        stem = p.rstrip("/")
        item_path = next((q for q in paths if "get" in paths[q] and len(PARAM.findall(q)) == 1
                          and re.fullmatch(re.escape(stem) + r"/\{[^}/]+\}/?", q)), None)  # fmt: skip
        if item_path:
            out.append({"collection": p, "item": item_path, "delete": "delete" in paths[item_path]})
    return sorted(out, key=lambda r: not r["delete"])  # stable: spec order otherwise


def _ordered(spec: dict) -> list[tuple[str, str, dict]]:
    """Resource operations first (full CRUD before the rest), then spec order (D11)."""
    rank = {}
    for i, r in enumerate(resources(spec)):
        rank.setdefault(r["collection"], i)
        rank.setdefault(r["item"], i)
    return sorted(_ops(spec), key=lambda o: rank.get(o[1], len(rank)))


def json_schema(spec: dict, op: dict) -> dict | None:
    s = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
    return resolve(spec, s) if s else None


def _param_names(spec: dict, op: dict) -> set[str]:
    return {resolve(spec, p).get("name") for p in op.get("parameters", []) + op["_shared"]}


def secured(spec: dict, op: dict, c: dict | None = None, key: str = "") -> bool:
    """The spec requires credentials: a non-empty security list without an empty (anonymous)
    alternative, on the operation or else globally. When the spec declares no security at all,
    the profile's discovered map decides (discover/002 P1): its default, unless the operation
    ("METHOD path") is public or was observed open (those stay spec-side until confirmed)."""
    sec = op.get("security", spec.get("security"))
    if sec is None and (m := (c or {}).get("secured")):
        return m.get("default") == "secured" and key not in m.get("public", []) \
            and key not in m.get("observed_open", [])  # fmt: skip
    return bool(sec) and {} not in sec


def _eligible(family: str, spec: dict, c: dict) -> list[tuple[str, str]]:
    ops = _ordered(spec)
    no_param = lambda p: not PARAM.search(p)
    pick = {
        # reads and creates only: a generated write to a random ID could hit other tests' data
        "properties": [(m, p) for m, p, op in ops if m == "GET" or (m == "POST" and no_param(p)
                       and (json_schema(spec, op) or not op.get("requestBody")))],
        "auth_required": [(m, p) for m, p, _ in ops],
        "unknown_id": [(m, p) for m, p, _ in ops if m == "GET" and len(PARAM.findall(p)) == 1
                       and p.rstrip("/").endswith("}")],
        "invalid_input": [(m, p) for m, p, op in ops if m == "POST" and no_param(p)
                          and (json_schema(spec, op) or {}).get("properties")],
        "read_back": [("POST", r["collection"]) for r in resources(spec)
                      if json_schema(spec, spec["paths"][r["collection"]]["post"])],
        "delete_final": [("POST", r["collection"]) for r in resources(spec) if r["delete"]
                         and json_schema(spec, spec["paths"][r["collection"]]["post"])],
        "paging": [(m, p) for m, p, op in ops if m == "GET" and no_param(p) and c["paging"]
                   and _param_names(spec, op) & {c["paging"].get(k) for k in ("page", "size", "token")}],
    }  # fmt: skip
    return pick[family]


def plan(spec: dict, c: dict, focus: list[str] = ()) -> list[tuple[str, str | None]]:
    """[(case id, exclusion reason or None)]: up to `per_family` operations per family, the run's
    own routes (`focus`, "METHOD path") first (checks/002). A case id is
    `G:<family>:<METHOD> <path>`, which is also its criterion id downstream."""
    excluded = {e["op"]: e["reason"] for e in c["exclude"]}
    first = set(focus)
    return [(f"G:{fam}:{m} {p}", excluded.get(f"{m} {p}"))
            for fam in FAMILIES
            for m, p in sorted(_eligible(fam, spec, c), key=lambda o: f"{o[0]} {o[1]}" not in first)
            [: c["per_family"]]]  # fmt: skip


# ---- requests and claims ---------------------------------------------------------------


def _send(method: str, path: str, *, token: str | None | bool = True, **kw):
    tok = api_token() if token is True else token
    r = api.request(method, path, token=tok or None, **kw)
    rec = record.current()
    rec.under_test = rec.exchanges[-1]  # the exchange the claim is about
    return r


def _fail(aid: str, msg: str, spec_side: bool = False):
    record.current().assertions.append({"id": aid, "ok": False, "expected": None, "actual": msg})
    raise (SpecMismatch if spec_side else ExpectationFailed)(aid, msg)


def _no_5xx(aid: str, r) -> None:
    if r.status_code >= 500:
        _fail(f"{aid}:5xx", f"{r.request.method} {r.request.url.path} answered {r.status_code}")


def _json(r):
    try:
        return r.json()
    except ValueError:
        return None


def _fill(path: str, value) -> str:
    return PARAM.sub(str(value).split("/")[-1], path)  # a resource name's last segment


def unknown(c: dict) -> str:
    n = random.randint(10**9, 2 * 10**9)
    return str(n) if c["id_style"] == "integer" else f"tp-no-such-{n:x}"


UNSET_ENUM = re.compile(r"(^|_)UNSPECIFIED$")  # AIP-126: an enum's zero value means "unset"


def _valid(spec: dict, schema: dict | None, name: str):
    s = resolve(spec, schema or {})
    if "enum" in s:
        # the first real value: an "unset" zero value (https://google.aip.dev/126) is replaced by
        # the server's default, so sending it would make read_back report a defect that isn't one
        return next((v for v in s["enum"] if not (isinstance(v, str) and UNSET_ENUM.search(v))),
                    s["enum"][0])  # fmt: skip
    t = s.get("type")
    if t == "string":
        v = record.current().name(name)
        return v[-s["maxLength"] :] if "maxLength" in s else v  # keep the unique tail
    return {"integer": int(s.get("minimum", 1)), "number": s.get("minimum", 1), "boolean": False,
            "array": [], "object": {}}.get(t)  # fmt: skip


def valid_body(spec: dict, schema: dict) -> dict:
    """Required fields (or else the first string field) with plain valid values."""
    props, req = schema.get("properties", {}), schema.get("required", [])
    keys = req or [k for k, p in props.items() if resolve(spec, p).get("type") == "string"][:1]
    return {k: _valid(spec, props.get(k), k) for k in keys}


def invalid_bodies(spec: dict, schema: dict) -> list[tuple[str, dict]]:
    props, req = schema.get("properties", {}), schema.get("required", [])
    out = [("missing required fields", {})] if req else []
    wrong = {k: WRONG[t] for k in req or props if (t := resolve(spec, props.get(k, {})).get("type")) in WRONG}  # fmt: skip
    if wrong:
        out.append(("wrong types", wrong))
    for k, p in props.items():
        s = resolve(spec, p)
        if s.get("type") == "string" and "maxLength" in s:
            out.append((f"{k} longer than maxLength {s['maxLength']}",
                        valid_body(spec, schema) | {k: "x" * (s["maxLength"] + 1)}))  # fmt: skip
            break
    return out


def _create(spec: dict, c: dict, collection: str) -> tuple[dict, str]:
    """POST a valid body; -> (body, item path). Skips (EXCLUDED) when no valid body is known."""
    res = next(r for r in resources(spec) if r["collection"] == collection)
    body = valid_body(spec, json_schema(spec, spec["paths"][collection]["post"]))
    r = _send("POST", collection, json=body)
    _no_5xx("create", r)
    if not r.is_success:
        pytest.skip(f"no_valid_body: POST {collection} answered {r.status_code}")
    created = _json(r)
    rid = created.get(c["id_field"]) if isinstance(created, dict) else None
    if rid is None:
        pytest.skip(f"async_create: POST {collection} answered no {c['id_field']!r}")
    return body, _fill(res["item"], rid)


# ---- the families ----------------------------------------------------------------------


def properties(spec, c, method, path, op):
    from hypothesis import HealthCheck, Phase, given, settings
    from schemathesis import checks
    from schemathesis.core import NotSet

    operation = _schemathesis(spec)[path][method]
    scheme = os.environ.get("TP_AUTH_SCHEME", "Bearer")
    rec = record.current()
    drift: list[dict] = []  # spec-only failures: recorded, and the examples go on (checks/002)

    @given(operation.as_strategy())
    @settings(max_examples=c["examples"], derandomize=True, database=None, deadline=None,
              phases=(Phase.explicit, Phase.generate), suppress_health_check=list(HealthCheck))  # fmt: skip
    def one(case):
        rec.exchanges.clear()  # keep the failing example's exchange, not every example's
        body = None if isinstance(case.body, NotSet) else case.body
        r = _send(case.method, case.formatted_path, params=case.query or None, json=body)
        if not hasattr(r, "_elapsed"):  # schemathesis reads it; mocked transports don't set it
            r.elapsed = __import__("datetime").timedelta(0)
        try:
            case.validate_response(r, checks=[checks.not_a_server_error,
                                              checks.status_code_conformance,
                                              checks.response_schema_conformance])  # fmt: skip
        except BaseExceptionGroup as g:  # schemathesis' FailureGroup
            seen = {"checks": sorted({f.title for f in g.exceptions}), "status": r.status_code,
                    "exchange": rec.exchanges[-1],
                    "curl": case.as_curl_command(headers={"Authorization": f"{scheme} <token>"},  # masked
                                                 base_url=os.environ.get("TP_BASE_URL"))}  # fmt: skip
            if "ServerError" not in {type(f).__name__ for f in g.exceptions}:
                drift.append(seen)  # a 5xx in a later example must not hide behind this one
                return
            rec.observed.update({k: v for k, v in seen.items() if k != "exchange"})
            _fail("property", f"{case.method} {case.formatted_path} → {r.status_code}: "
                  f"{', '.join(seen['checks'])}")  # fmt: skip

    one()
    if drift:  # every example ran with no 5xx: report the drift, shown by its first example
        first = drift[0]
        rec.exchanges[:] = [first["exchange"]]
        rec.under_test = first["exchange"]
        rec.observed.update({"checks": sorted({t for d in drift for t in d["checks"]}),
                             "status": first["status"], "curl": first["curl"],
                             "examples": f"{len(drift)}/{c['examples']}"})  # fmt: skip
        _fail("property", f"{method} {path}: {len(drift)} example(s) disagree with the spec "
              f"({', '.join(rec.observed['checks'])}); first {first['exchange']['path']} → "
              f"{first['status']}", spec_side=True)  # fmt: skip


def auth_required(spec, c, method, path, op):
    sec = secured(spec, op, c, f"{method} {path}")
    if f"{method} {path}" in (c.get("secured") or {}).get("public", []):
        pytest.skip("public: the sign-in or first-user operation (discover/002 P1)")
    for label, token in (("no credentials", None), ("an invalid token", "tp-invalid-token")):
        r = _send(method, _fill(path, unknown(c)), token=token)
        _no_5xx(label, r)
        if r.is_success:
            _fail(f"{label}:2xx", f"{method} {r.request.url.path} answered {r.status_code} with "
                  f"{label}" + ("" if sec else "; the spec declares no security for it"),
                  spec_side=not sec)  # fmt: skip


def unknown_id(spec, c, method, path, op):
    r = _send("GET", _fill(path, unknown(c)))
    _no_5xx("read", r)
    if r.is_success:
        _fail("2xx", f"GET {r.request.url.path}: an ID that doesn't exist answered {r.status_code}")


def invalid_input(spec, c, method, path, op):
    for label, body in invalid_bodies(spec, json_schema(spec, op)):
        r = _send("POST", path, json=body)
        _no_5xx(label, r)
        if r.is_success:
            _fail(f"{label}:2xx", f"POST {path} accepted a body its spec forbids ({label}): "
                  f"{r.status_code}", spec_side=True)  # fmt: skip


def _write_only(spec: dict, schema: dict | None) -> set[str]:
    return {k for k, p in (schema or {}).get("properties", {}).items()
            if (s := resolve(spec, p)).get("writeOnly") or s.get("format") == "password"}  # fmt: skip


def _masked(v) -> bool:
    """A secret the server masks on read (`*****`): not a round-trip failure.
    shortcut: mask characters only; upgrade when a target masks some other way."""
    return isinstance(v, str) and len(v) > 0 and set(v) <= set("*•")


def read_back(spec, c, method, path, op):
    body, item = _create(spec, c, path)
    r = eventually(lambda: _send("GET", item), lambda r: r.status_code == 200)
    _no_5xx("read", r)
    if r.status_code != 200:
        _fail("read", f"GET {item} after its create answered {r.status_code}")
    got = _json(r) if isinstance(_json(r), dict) else {}
    secret = _write_only(spec, json_schema(spec, op))
    if diff := {k: [v, got[k]] for k, v in body.items()
                if k in got and got[k] != v and k not in secret and not _masked(got[k])}:  # fmt: skip
        _fail("round_trip", f"GET {item}: declared fields did not round-trip (sent, read): {diff}")


def delete_final(spec, c, method, path, op):
    _, item = _create(spec, c, path)
    r = _send("DELETE", item)
    _no_5xx("delete", r)
    if not r.is_success:
        pytest.skip(f"delete_refused: DELETE {item} answered {r.status_code}")
    r = _send("GET", item)
    _no_5xx("read", r)
    if r.is_success:
        _fail("read_after_delete", f"GET {item} after its DELETE answered {r.status_code}")


def paging(spec, c, method, path, op):
    pg, names = c["paging"], _param_names(spec, op)
    for key, value in PAGING[pg["style"]]:
        if (name := pg.get(key)) not in names:
            continue
        r = _send("GET", path, params={name: value})
        _no_5xx(f"{name}={value}", r)
        body = _json(r)
        page = isinstance(body, list) or (isinstance(body, dict)
                                          and any(isinstance(v, list) for v in body.values()))  # fmt: skip
        if r.is_success and not page:
            _fail(f"{name}={value}:shape", f"GET {path}?{name}={value} answered {r.status_code} "
                  "without a list of items")  # fmt: skip


RUN = {f: globals()[f] for f in FAMILIES}
_st_schema = None


def _schemathesis(spec: dict):
    global _st_schema
    if _st_schema is None:
        import schemathesis

        _st_schema = schemathesis.openapi.from_dict(spec)
    return _st_schema


def _spec() -> dict:
    from platform_rt.plugin import _openapi

    return _openapi() or {}


def run(case_id: str, spec: dict, c: dict) -> None:
    _, family, route = case_id.split(":", 2)
    method, path = route.split(" ", 1)
    op = next(o for m, p, o in _ops(spec) if (m, p) == (method, path))
    RUN[family](spec, c, method, path, op)


# ---- pytest wiring: the generated file star-imports these two --------------------------


def pytest_generate_tests(metafunc):
    if metafunc.function.__name__ == "test_generic":
        c = conf()
        focus = getattr(metafunc.module, "TP_FOCUS", [])  # the run's routes, from generate
        params = [pytest.param(cid, id=cid, marks=[pytest.mark.skip(reason=f"{why}: excluded by the profile")] if why else [])
                  for cid, why in plan(_spec(), c, focus)]  # fmt: skip
        metafunc.parametrize("case_id", params)


def test_generic(case_id):
    run(case_id, _spec(), conf())
