import json

import httpx
import pytest
from platform_rt.blackbox import Make

from platform_rt import api, eventually, record


@pytest.fixture(autouse=True)
def rec(monkeypatch):
    monkeypatch.setenv("TP_RUN", "r-9")
    record.start("t.py::test_x", "TC-1")
    yield
    api.use(None)


def _use(handler):
    api.use(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t"))


def test_names_are_unique_per_test_execution():
    a = Make().name("tag")
    record.start("t.py::test_x", "TC-1")  # the gate's next repetition of the same test
    b = Make().name("tag")
    assert a.startswith("tp-r-9-") and a.endswith("-tag") and a != b


def test_creates_go_to_the_ledger_and_the_auth_scheme_is_configurable(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        if req.method == "POST" and req.url.path == "/api/tags/":
            return httpx.Response(201, json={"id": 7})
        return httpx.Response(400 if req.method == "POST" else 200, json={})

    _use(handler)
    monkeypatch.setenv("TP_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("TP_AUTH_SCHEME", "Token")
    api.post("/api/tags/", token="abc", json={"name": "n"})
    api.post("/api/bad/", json={})  # failed create: not recorded
    api.get("/api/tags/")  # reads: not recorded
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert [json.loads(x) for x in lines] == [{"path": "/api/tags/", "id": 7, "location": None}]
    assert seen[0] == "Token abc" and seen[1] is None


def test_eventually_retries_until_the_condition_holds_and_records_it():
    calls = iter([404, 404, 200])
    _use(lambda req: httpx.Response(next(calls), json={"id": 1}))
    r = eventually(lambda: api.get("/api/documents/1/"), lambda r: r.status_code == 200, 5)
    assert r.status_code == 200 and record.current().retries["attempts"] == 2


def test_eventually_gives_up_at_the_timeout_and_returns_the_last_result():
    _use(lambda req: httpx.Response(404))
    r = eventually(lambda: api.get("/x"), lambda r: r.json()["never"], timeout=0.3)
    assert r.status_code == 404 and record.current().retries["waited_s"] <= 0.5


def test_a_test_deletes_its_own_creates_at_teardown(tmp_path, monkeypatch):
    from platform_rt import blackbox, plugin

    deleted = []

    def handler(req):
        if req.method == "DELETE":
            deleted.append(req.url.path)
            return httpx.Response(204)
        if req.url.path == "/api/documents/post_document/":
            return httpx.Response(200, json="a-task-uuid")  # async create: no id
        return httpx.Response(201, json={"id": int(req.url.path.split("/")[2] == "tags") + 6})

    _use(handler)
    for k, v in {"TP_LEDGER": str(tmp_path / "l.jsonl"), "TP_SECRET_TOKEN": "t",
                 "TP_AUTH_SCHEME": "Token"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(plugin, "_schema", {"paths": {"/api/tags/{id}/": {"delete": {}}}})
    api.post("/api/tags/", json={"name": "invoices"})
    api.post("/api/notes/", json={})  # no DELETE route: left for the platform's cleanup
    api.post("/api/documents/post_document/")
    blackbox.pytest_runtest_teardown(None)
    assert deleted == ["/api/tags/7/"]
    lines = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert lines[2]["untracked"] == "a-task-uuid"
    assert lines[-1] == {"path": "/api/tags/", "id": 7, "location": None, "deleted_by_test": True}
