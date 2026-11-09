"""HTTP client for generated tests. Every request is recorded and carries `traceparent` and
`X-Test-Trace: <run>/<tc>/<step>` (plan Part 5, Tracing). In-process the harness hands in a
TestClient (`api.use`); over the network it talks to $TP_BASE_URL."""

import hashlib
import json
import os
import time

import httpx

from platform_rt import record

ABSENT = "<absent>"  # a case_data value meaning: this key must not be sent


class ConformanceError(Exception):
    """The request under test did not carry case_data exactly (plan G3 ConformanceCheck)."""


def _parse(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]


def _query_value(v) -> str:
    return json.dumps(v) if isinstance(v, bool) else str(v)  # true/false, like the wire


def conforms(exchange: dict, case_data: dict) -> list[str]:
    """Keys of case_data the request got wrong ([] = conformant). Query values compare as
    strings (that is all a query string is); JSON body values compare exactly, type included."""
    body = exchange["json"] if isinstance(exchange["json"], dict) else {}
    query = exchange["query"]
    wrong = []
    for k, v in case_data.items():
        if v == ABSENT:
            if k in query or k in body:
                wrong.append(k)
        elif k in body:
            if json.dumps(body[k], sort_keys=True) != json.dumps(v, sort_keys=True):
                wrong.append(k)
        elif query.get(k) != _query_value(v) or v is None:
            wrong.append(k)
    return wrong


def route_matches(route: str, method: str, path: str) -> bool:
    """`GET /api/notes/{id}` matches GET /api/notes/3f2a…"""
    r_method, _, r_path = route.partition(" ")
    want, got = r_path.split("/"), path.split("/")
    return (
        r_method.upper() == method.upper()
        and len(want) == len(got)
        and all(
            w == g or (w.startswith("{") and w.endswith("}") and g != "")  # a param is never empty
            for w, g in zip(want, got, strict=True)
        )
    )


def _fits(exchange: dict, request: dict) -> bool:
    routes, auth = request.get("routes"), request.get("auth")
    if routes and not any(route_matches(r, exchange["method"], exchange["path"]) for r in routes):
        return False
    return auth is None or exchange["auth"] == auth


def _summary(e: dict) -> str:
    return (f"{e['method']} {e['path']} query={e['query']} json={e['json']} "
            f"token={'yes' if e['auth'] else 'no'}")  # fmt: skip


def dig(body, field: str):
    """A dotted field (`user.token`, `article.slug`) in a JSON answer; None when it isn't there."""
    for k in field.split("."):
        body = body.get(k) if isinstance(body, dict) else None
    return body


def _ledger(method: str, resp: httpx.Response) -> None:
    """$TP_LEDGER: every created resource, so namespace state can delete it at end of run (D1)."""
    path = os.environ.get("TP_LEDGER")
    if not path or method.upper() != "POST" or not resp.is_success:
        return
    body = _parse(resp)
    rid = dig(body, os.environ.get("TP_ID_FIELD") or "id")
    entry = {"path": resp.request.url.path, "id": rid, "location": resp.headers.get("location")}
    if rid is None and entry["location"] is None:  # e.g. an async create answering a task id:
        entry["untracked"] = str(body)[:120]  # listed as a leftover, never silently missed
    else:
        record.current().created.append(entry)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


class Api:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._last = 0.0  # $TP_RATE: requests per second, so rate limiters don't skew results

    def _pace(self) -> None:
        if rate := float(os.environ.get("TP_RATE") or 0):
            time.sleep(max(0.0, self._last + 1 / rate - time.monotonic()))
            self._last = time.monotonic()

    def use(self, client: httpx.Client | None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=os.environ["TP_BASE_URL"], timeout=30)
        return self._client

    def request(self, method: str, path: str, *, token: str | None = None, params=None,
                json=None, data=None, files=None, setup: bool = False) -> httpx.Response:  # fmt: skip
        rec = record.current()
        step = rec.next_step()
        span = hashlib.sha256(f"{rec.trace_id}/{step}".encode()).hexdigest()[:16]
        headers = {
            "traceparent": f"00-{rec.trace_id}-{span}-01",
            "X-Test-Trace": f"{rec.run}/{rec.tc}/{step}",
        }
        if token:  # TP_AUTH_SCHEME: Bearer (default), Token, Basic (blackbox D7)
            headers["Authorization"] = f"{os.environ.get('TP_AUTH_SCHEME', 'Bearer')} {token}"
        # Gate: a mutant is active only for the test's own steps. Setup (the factories) runs
        # unmutated, so a kill means the test's request exposed the mutant, not that the
        # mutant broke the test data (mutmut's trampoline reads this variable on every call).
        mutant = os.environ.pop("MUTANT_UNDER_TEST", None) if setup else None
        self._pace()
        try:
            resp = self.client.request(method, path, params=params, json=json, data=data,
                                       files=files, headers=headers)  # fmt: skip
        finally:
            if mutant is not None:
                os.environ["MUTANT_UNDER_TEST"] = mutant
        if rule := resp.headers.get("x-tp-blocked"):  # the safe-mode proxy refused to send it
            import pytest

            pytest.skip(f"NOT_RUN: safe mode ({rule}): {method.upper()} {resp.request.url.path}")
        _ledger(method, resp)
        rec.exchanges.append({
            "step": step, "setup": setup, "method": method.upper(), "path": resp.request.url.path,
            "query": dict(resp.request.url.params), "json": json, "form": data, "auth": bool(token),
            "status": resp.status_code, "body": _parse(resp),
            "traceparent": headers["traceparent"], "x_test_trace": headers["X-Test-Trace"],
        })  # fmt: skip
        return resp

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> httpx.Response:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw) -> httpx.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.request("DELETE", path, **kw)

    def under_test(self, case_data: dict | None = None, request: dict | None = None) -> dict:
        """The exchange the locked block asserts on: the last test request that goes to one of
        the case's routes with the required auth (`request`) and carries case_data exactly.
        With TP_CONFORMANCE=0 (guards v1): simply the last test request."""
        rec = record.current()
        rec.case_data = case_data
        steps = [e for e in rec.exchanges if not e["setup"]]
        if not steps:
            raise ConformanceError("the binding sent no request")
        if os.environ.get("TP_CONFORMANCE", "1") == "0":
            rec.under_test = steps[-1]
            return steps[-1]
        fitting = [e for e in steps if _fits(e, request or {})]
        if not fitting:
            raise ConformanceError(
                f"no request went to {request['routes']} with token="
                f"{ {None: 'any', True: 'yes', False: 'no'}[request.get('auth')] }; last sent "
                f"{_summary(steps[-1])}"
            )
        for e in reversed(fitting):
            if not case_data or not conforms(e, case_data):
                rec.under_test = e
                return e
        last = fitting[-1]
        raise ConformanceError(
            f"no request carried case_data {case_data} exactly; last sent {_summary(last)} "
            f"(wrong: {conforms(last, case_data)})"
        )

    def openapi(self) -> dict:
        if f := os.environ.get("TP_OPENAPI_FILE"):  # black-box: the platform's copy (discover/002)
            with open(f) as fh:
                return json.load(fh)
        path = os.environ.get("TP_OPENAPI_PATH") or (
            os.environ.get("TP_API_BASE", "") + "/openapi.json"  # the env stage sets it
        )
        return self.client.get(path).json()


api = Api()

REFRESH_S = 60  # log in again when a token has less than this left (discover/002 P7)


def _expiry(token: str) -> float | None:
    """A JWT's `exp` (RFC 7519); None for any other token."""
    import base64

    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except IndexError, ValueError, KeyError, TypeError:
        return None


def token() -> str | None:
    """The admin token ($TP_SECRET_TOKEN). With $TP_LOGIN set, a JWT about to expire is replaced
    by a fresh login before it is used, never after a 401 (so a 401 stays evidence)."""
    tok = os.environ.get("TP_SECRET_TOKEN")
    login = os.environ.get("TP_LOGIN")
    exp = _expiry(tok or "")
    if not (tok and login and exp is not None and exp - time.time() < REFRESH_S):
        return tok
    spec = json.loads(login)
    body = json.loads(
        json.dumps(spec["body"]).replace(
            "$TP_SECRET_ADMIN_PASSWORD",
            json.dumps(os.environ.get("TP_SECRET_ADMIN_PASSWORD", ""))[1:-1],
        )
    )
    kw = {"json": body} if spec.get("json") else {"data": body}
    tok = api.client.post(spec["path"], **kw).json()
    for k in spec["token_field"].split("."):  # dotted: `user.token` (the host's blackbox._dig)
        tok = tok[k]
    tok = str(tok)
    os.environ["TP_SECRET_TOKEN"] = tok
    return tok
