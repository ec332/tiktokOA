"""Dependency check families (docs/status/standins/001 D7, D8): does the SUT call its outbound
dependencies correctly, and does it survive their failure? The dependencies are SIMULATED by
local stand-ins (C8), so every verdict here is labelled simulated (C2).

`$TP_FAMILIES` carries `standins: [{name, kind, url, journal}]` (P2, reach/001 D9) and
`dependencies`: the wired entries discovery verified (C5), each with the `trigger` op that
reaches its stand-in and an optional `setup` call that registers it."""

import json
import time

import httpx
import pytest

from platform_rt import families as f
from platform_rt import record
from platform_rt.blackbox import eventually
from platform_rt.origin import resolve

GRACEFUL = (502, 503, 504)  # "the dependency failed": the answer we want under a fault
DELAY_S, DEADLINE_S = 8.0, 4.0  # the HTTP fault, and how long the trigger may take under it
SMTP_REFUSAL = 451  # Mailpit chaos (verified on Mailpit v1.33.0, standins/001 D8)


def _dep(c: dict, method: str, path: str) -> dict:
    # shortcut: one dependency per trigger op; key case ids by dependency when one op has two
    return next(d for d in c.get("dependencies", []) if d.get("trigger") == f"{method} {path}")


def _standin(c: dict, dep: dict) -> dict:
    return next(s for s in c.get("standins", []) if s["name"] == dep["name"])


def received(c: dict, dep: dict) -> list[dict]:
    """What the dependency's stand-in received: HTTP requests on its path, or SMTP messages."""
    s = _standin(c, dep)
    r = httpx.get(s["url"].rstrip("/") + s["journal"], timeout=10)
    r.raise_for_status()
    if dep["kind"] == "smtp":
        # shortcut: Mailpit's first page (newest 50); page it when a run sends more mail
        return [{"to": [a["Address"] for k in ("To", "Cc", "Bcc") for a in m.get(k) or []],
                 "from": (m.get("From") or {}).get("Address"), "subject": m.get("Subject")}
                for m in r.json().get("messages", [])]  # fmt: skip
    path = "/" + dep["key"].lower()  # each HTTP dependency has its own path on the fake (D6)
    return [e for e in r.json() if e["path"].split("?")[0] == path]


def fault(c: dict, dep: dict, on: bool) -> dict | None:
    """Set (or clear) the stand-in's fault; -> the record's `fault` (C1)."""
    s, base = _standin(c, dep), _standin(c, dep)["url"].rstrip("/")
    if dep["kind"] == "smtp":
        body = {"Recipient": {"ErrorCode": SMTP_REFUSAL, "Probability": 100}} if on else {}
        httpx.put(base + "/api/v1/chaos", json=body, timeout=10).raise_for_status()
        return {"standin": s["name"], "mode": "smtp", "value": SMTP_REFUSAL} if on else None
    delay = float(c.get("dependency_delay_s", DELAY_S))
    httpx.post(base + "/__fault", json={"delay_s": delay} if on else {},
               timeout=10).raise_for_status()  # fmt: skip
    return {"standin": s["name"], "mode": "delay", "value": delay} if on else None


def _setup(dep: dict) -> None:
    """A webhook registered through the API (`via: spec`): register the stand-in first."""
    if s := dep.get("setup"):
        m, p = s["op"].split(" ", 1)
        r = f.api.request(m, p, token=f.api_token(), json=s["body"], setup=True)
        if not r.is_success:
            pytest.skip(f"setup_refused: {s['op']} answered {r.status_code}")


def _body(spec: dict, op: dict) -> dict:
    """A valid body whose strings are all unique to this test (`format: email` stays an email)."""
    schema = f.json_schema(spec, op)
    if not schema:
        return {}
    body = f.valid_body(spec, schema)
    for k, p in schema.get("properties", {}).items():
        if k in body and resolve(spec, p).get("format") == "email":
            body[k] = f"{record.current().name(k)}@example.test"
    return body


def dependency_called(spec, c, method, path, op):
    dep, rec = _dep(c, method, path), record.current()
    _setup(dep)
    body = _body(spec, op)
    r = f._send(method, path, json=body or None)
    f._no_5xx("trigger", r)
    if not r.is_success:
        pytest.skip(f"trigger_refused: {method} {path} answered {r.status_code}")
    want = [v for v in body.values() if isinstance(v, str)]
    got = eventually(lambda: received(c, dep), lambda g: all(v in json.dumps(g) for v in want))
    mine = [g for g in got if any(v in json.dumps(g) for v in want)]
    rec.observed["received"] = mine or got[-3:]  # redacted with the record (D9)
    if missing := [v for v in want if v not in json.dumps(got)]:
        f._fail("received", f"{method} {path}: stand-in {dep['name']} ({dep['key']}) never "
                f"received {missing}; it received {len(got)} item(s) there")  # fmt: skip


def dependency_failure_handled(spec, c, method, path, op):
    dep, rec = _dep(c, method, path), record.current()
    _setup(dep)
    body = _body(spec, op)
    rec.fault = fault(c, dep, True)
    try:
        t0 = time.monotonic()
        r = f._send(method, path, json=body or None)
        took = time.monotonic() - t0
    finally:
        fault(c, dep, False)
    deadline = float(c.get("dependency_deadline_s", DEADLINE_S))
    if took > deadline:
        f._fail("in_time", f"{method} {path} took {took:.1f}s with {dep['name']} failing "
                f"({rec.fault['mode']} {rec.fault['value']}); the limit is {deadline:.0f}s")  # fmt: skip
    if r.status_code >= 500 and r.status_code not in GRACEFUL:
        f._fail("5xx", f"{method} {path} answered {r.status_code} with {dep['name']} failing; "
                f"{'/'.join(map(str, GRACEFUL))} is the graceful answer")  # fmt: skip
    res = next((x for x in f.resources(spec) if x["collection"] == path), None)
    created = f._json(r) if r.is_success else None
    if res and isinstance(created, dict) and (rid := created.get(c["id_field"])) is not None:
        read = f._send("GET", f._fill(res["item"], rid))
        if read.status_code != 200:
            f._fail("read_back", f"GET {read.request.url.path} answered {read.status_code} "
                    f"after its create with {dep['name']} failing")  # fmt: skip


def eligible(c: dict) -> list[tuple[str, str]]:
    return [
        tuple(d["trigger"].split(" ", 1)) for d in c.get("dependencies", []) if d.get("trigger")
    ]


def ledger(results: list[dict]) -> dict:
    """`dependencies.json` (D9): per dependency check, its outcome, fault and what was received."""
    return {r["case_id"]: {"outcome": r["outcome"], "fault": r.get("fault"),
                           "received": (r.get("observed") or {}).get("received", [])}
            for r in results if str(r.get("case_id")).startswith("G:dependency_")}  # fmt: skip
