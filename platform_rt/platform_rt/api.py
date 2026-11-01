"""HTTP client for generated tests. Every request is recorded and carries `traceparent` and
`X-Test-Trace: <run>/<tc>/<step>` (plan Part 5, Tracing). In-process the harness hands in a
TestClient (`api.use`); over the network it talks to $TP_BASE_URL."""

import hashlib
import json
import os

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
    """`GET /api/v1/items/{id}` matches GET /api/v1/items/3f2a…"""
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


class Api:
    def __init__(self):
        self._client: httpx.Client | None = None

    def use(self, client: httpx.Client | None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=os.environ["TP_BASE_URL"], timeout=30)
        return self._client

    def request(self, method: str, path: str, *, token: str | None = None, params=None,
                json=None, data=None, setup: bool = False) -> httpx.Response:  # fmt: skip
        rec = record.current()
        step = rec.next_step()
        span = hashlib.sha256(f"{rec.trace_id}/{step}".encode()).hexdigest()[:16]
        headers = {
            "traceparent": f"00-{rec.trace_id}-{span}-01",
            "X-Test-Trace": f"{rec.run}/{rec.tc}/{step}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # Gate: a mutant is active only for the test's own steps. Setup (the factories) runs
        # unmutated, so a kill means the test's request exposed the mutant, not that the
        # mutant broke the test data (mutmut's trampoline reads this variable on every call).
        mutant = os.environ.pop("MUTANT_UNDER_TEST", None) if setup else None
        try:
            resp = self.client.request(method, path, params=params, json=json, data=data,
                                       headers=headers)  # fmt: skip
        finally:
            if mutant is not None:
                os.environ["MUTANT_UNDER_TEST"] = mutant
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
        path = os.environ.get("TP_API_BASE", "/api/v1") + "/openapi.json"
        return self.client.get(path).json()


api = Api()
