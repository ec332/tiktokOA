"""Safe-mode proxy (external/001 E1-E5): a fake target records every request that reaches it;
only the ones the policy allows ever do."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from platform_rt.api import api
from platform_rt.proxy import Policy, server

SPEC = {"paths": {
    "/api/items/": {"get": {}, "post": {}},
    "/api/items/{id}/": {"get": {}, "put": {}, "delete": {}},
    "/api/items/bulk_edit/": {"post": {}},
    "/api/users/": {"get": {}, "post": {}},
    "/api/users/{id}/": {"get": {}, "delete": {}},
    "/api/notes/": {"post": {}},  # no item path: a create that can't be cleaned up
    "/api/tasks/": {"post": {"x-required-scope": ["admin"]}},
    "/api/tasks/{id}/": {"get": {}, "delete": {}},
}}  # fmt: skip
DENY = ["user", "bulk", "admin"]


@pytest.fixture
def stack(tmp_path):
    seen, ids = [], iter(range(1, 100))

    class Fake(BaseHTTPRequestHandler):
        def _any(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            seen.append((self.command, self.path, body.decode()))
            out = json.dumps({"id": next(ids)} if self.command == "POST" else {}).encode()
            self.send_response(201 if self.command == "POST" else 200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any

        def log_message(self, *a):
            pass

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    ledger = tmp_path / "ledger.jsonl"
    policy = Policy(SPEC, DENY, "r1", ledger=str(ledger))
    up = httpx.Client(base_url=f"http://127.0.0.1:{fake.server_port}")
    proxy = server(policy, up, port=0)
    for s in (fake, proxy):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{proxy.server_port}") as c:
        yield c, seen, ledger
    for s in (fake, proxy):
        s.shutdown()


def test_no_unsafe_request_leaves_the_platform(stack):
    c, seen, ledger = stack
    tagged = {"name": "tp-r1-ab12-item"}
    battery = [
        ("GET", "/api/items/", None, "read"),
        ("POST", "/api/items/", tagged, "create"),  # -> id 1
        ("POST", "/api/items/", {"name": "plain"}, "doesn't carry the run prefix"),
        ("POST", "/api/users/", tagged, "deny category 'user'"),
        ("POST", "/api/items/bulk_edit/", tagged, "deny category 'bulk'"),
        ("POST", "/api/notes/", tagged, "not a collection with read and delete"),
        ("POST", "/api/tasks/", tagged, "deny category 'admin'"),  # the scope marker
        ("PUT", "/api/items/1/", tagged, "created by this run"),
        ("DELETE", "/api/items/7/", None, "not created by this run"),  # someone else's data
        ("PATCH", "/api/elsewhere/", {}, "not in the spec"),
        ("DELETE", "/api/items/1/", None, "created by this run"),
        ("PUT", "/api/items/1/", tagged, "not created by this run"),  # deleted: not ours anymore
    ]
    allowed = []
    for method, path, body, rule in battery:
        r = c.request(method, path, json=body)
        if r.status_code == 599:
            assert rule in r.headers["x-tp-blocked"], (method, path, r.headers["x-tp-blocked"])
        else:
            assert rule in ("read", "create", "created by this run"), (method, path)
            allowed.append((method, path))
    assert [(m, p) for m, p, _ in seen] == allowed == [
        ("GET", "/api/items/"), ("POST", "/api/items/"), ("PUT", "/api/items/1/"),
        ("DELETE", "/api/items/1/")]  # fmt: skip
    lines = [json.loads(ln) for ln in ledger.read_text().splitlines()]
    assert lines == [{"path": "/api/items/", "id": 1, "location": None},
                     {"path": "/api/items/", "id": 1, "location": None, "deleted_by_test": True}]  # fmt: skip
    assert Policy(SPEC, DENY, "r2", ledger=str(ledger)).owned == {}  # a restart sees it gone


def test_a_restarted_proxy_still_owns_what_an_earlier_batch_created(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"path": "/api/items/", "id": 4, "location": None}) + "\n")
    p = Policy(SPEC, DENY, "r3", ledger=str(ledger))
    assert p.decide("DELETE", "/api/items/4/") == (True, "created by this run")
    assert p.decide("DELETE", "/api/items/5/")[0] is False


def test_a_blocked_request_skips_the_test_as_not_run():
    blocked = httpx.MockTransport(
        lambda r: httpx.Response(599, headers={"X-TP-Blocked": "not created by this run"})
    )
    api.use(httpx.Client(base_url="http://t", transport=blocked))
    try:
        with pytest.raises(pytest.skip.Exception, match=r"NOT_RUN: safe mode \(not created"):
            api.delete("/api/items/9/")
    finally:
        api.use(None)
