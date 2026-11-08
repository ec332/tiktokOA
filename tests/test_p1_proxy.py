"""P1 proof tests for the safe-mode proxy (harden/001 H2, H3, H6; review H3, M1, M3, M4, L4).
A fake upstream records every request that reaches it."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from platform_rt.proxy import Policy, server

SPEC = {"paths": {
    "/api/items/": {"get": {}, "post": {}},
    "/api/items/{id}/": {"get": {}, "put": {}, "delete": {}},
    "/api/logout/": {"get": {}},
}}  # fmt: skip


@pytest.fixture
def stack():
    seen, answer = [], {"status": 201, "body": {"id": 1}}

    class Fake(BaseHTTPRequestHandler):
        def _any(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            seen.append((self.command, self.path, dict(self.headers)))
            out = json.dumps(answer["body"]).encode()
            self.send_response(answer["status"])
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = do_PUT = do_DELETE = _any

        def log_message(self, *a):
            pass

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    policy = Policy(SPEC, ["logout", "export"], "r1")
    up = httpx.Client(base_url=f"http://127.0.0.1:{fake.server_port}")
    proxy = server(policy, up, port=0, host="app.local:8000")
    for s in (fake, proxy):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{proxy.server_port}") as c:
        yield c, seen, policy, answer, proxy.server_port, fake.server_port
    for s in (fake, proxy):
        s.shutdown()


def _raw(port: int, target: str) -> bytes:
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\nAuthorization: Token S\r\n\r\n".encode())
        return s.recv(500)


def test_an_absolute_form_get_is_refused(stack):
    _, seen, _, _, port, fport = stack
    answer = _raw(port, f"http://127.0.0.1:{fport}/steal")
    assert answer.startswith(b"HTTP/1.0 599") and b"origin-form" in answer, answer
    assert seen == []
    _raw(port, "//evil.example/steal")  # the stdlib server folds `//` to `/`: stays on upstream
    assert [p for _, p, _ in seen] == ["/evil.example/steal"]


def test_method_overrides_are_stripped_and_method_param_refused(stack):
    c, seen, *_ = stack
    c.get("/api/items/", headers={"X-HTTP-Method-Override": "DELETE", "X-HTTP-Method": "DELETE"})
    assert not {"X-HTTP-Method-Override", "X-HTTP-Method"} & set(seen[-1][2])
    n = len(seen)
    for r in (c.get("/api/items/?_method=DELETE"),
              c.post("/api/items/", data={"_method": "DELETE", "name": "tp-r1-x"}),
              c.post("/api/items/", json={"_method": "DELETE", "name": "tp-r1-x"})):  # fmt: skip
        assert r.status_code == 599 and "_method" in r.headers["x-tp-blocked"]
    assert len(seen) == n


def test_a_200_answer_to_a_create_is_not_owned(stack):
    c, _, policy, answer, *_ = stack
    answer.update(status=200, body={"id": 5, "name": "inbox"})  # get-or-create: someone else's
    c.post("/api/items/", json={"name": "tp-r1-x"})
    assert policy.owned == {}
    answer.update(status=200, body={"id": 6, "name": "tp-r1-x"})  # 200, but it is ours
    c.post("/api/items/", json={"name": "tp-r1-x"})
    assert set(policy.owned) == {"/api/items/6/"}


def test_deny_words_apply_to_reads(stack):
    c, seen, *_ = stack
    for path in ("/api/logout/", "/api/export/run"):  # in the spec, and not
        r = c.get(path)
        assert r.status_code == 599 and "deny category" in r.headers["x-tp-blocked"]
    assert seen == []


def test_the_upstream_host_is_kept(stack):
    c, seen, *_ = stack
    c.get("/api/items/")
    assert seen[-1][2]["Host"] == "app.local:8000"
