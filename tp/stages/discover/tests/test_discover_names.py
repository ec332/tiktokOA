"""Discovery on a scripted fake with grpc-gateway-like conventions (discover/002 §2): a repo spec
declaring no security, a nested sign-in, the first user created anonymously, short-lived JWTs,
resource names and token paging. Plus `--no-conventions`, `setup` questions and
`needs_dependency`. No target names: the fake is a generic "notes" service."""

import base64
import json
import time

import httpx
import pytest
import yaml

from tp.core import artifacts, llm, secrets
from tp.core.contract import StageContext
from tp.stages.discover import compare
from tp.stages.discover.stage import Discover, files
from tp.stages.discover.tests import test_discover as drf
from tp.stages.env import blackbox as bb

SLUG = "notes-server"
ERR = lambda code, msg, status: httpx.Response(status, json={"code": code, "message": msg,
                                                             "details": []})  # fmt: skip

SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/api/auth/signin": {"post": {
            "requestBody": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/SignInRequest"}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/SignInResponse"}}}}}}},
        "/api/users": {"post": {
            "description": "The first user of an instance becomes an admin.",
            "requestBody": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/User"}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/User"}}}}}}},
        "/api/notes": {
            "get": {"parameters": [{"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
                                   {"name": "pageToken", "in": "query", "schema": {"type": "string"}},
                                   {"name": "filter", "in": "query", "schema": {"type": "string"}}]},
            "post": {"requestBody": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/Note"}}}}}},
        "/api/notes/{note}": {"get": {}, "delete": {}},
        "/api/instance/profile": {"get": {"description": "Public."}},
        "/api/stats": {"get": {}},
        "/api/ai/models": {"get": {}},
    },
    "components": {"schemas": {
        "SignInRequest": {"type": "object", "properties": {
            "passwordCredentials": {"$ref": "#/components/schemas/PasswordCredentials"},
            "ssoCredentials": {"type": "object", "properties": {"code": {"type": "string"}}}}},
        "PasswordCredentials": {"type": "object", "properties": {
            "username": {"type": "string"}, "password": {"type": "string"}}},
        "SignInResponse": {"type": "object", "properties": {"accessToken": {"type": "string"},
                                                            "user": {"type": "object"}}},
        "User": {"type": "object", "required": ["username", "role", "state"], "properties": {
            "name": {"type": "string"}, "username": {"type": "string"},
            "password": {"type": "string", "writeOnly": True},
            "role": {"type": "string", "enum": ["ROLE_UNSPECIFIED", "ADMIN", "USER"]},
            "state": {"type": "string", "enum": ["STATE_UNSPECIFIED", "NORMAL"]},
            "createTime": {"type": "string", "readOnly": True}}},
        "Note": {"type": "object", "required": ["content", "visibility"], "properties": {
            "name": {"type": "string"}, "creator": {"type": "string", "readOnly": True},
            "content": {"type": "string"},
            "visibility": {"type": "string", "enum": ["VISIBILITY_UNSPECIFIED", "PRIVATE"]}}},
    }},
}  # fmt: skip


def jwt(user: str, ttl: int = 900) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    now = int(time.time())
    return f"{enc({'alg': 'HS256'})}.{enc({'sub': user, 'iat': now, 'exp': now + ttl})}.sig"


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "upstream" / SLUG
    (r / "scripts").mkdir(parents=True)
    (r / "proto" / "gen").mkdir(parents=True)
    (r / "server").mkdir()
    (r / "scripts" / "compose.yaml").write_text(yaml.safe_dump({"services": {"app": {
        "image": "example/notes:stable", "ports": ["5230:5230"],
        "volumes": ["~/.notes/:/var/opt/notes"]}}}))  # fmt: skip
    (r / "proto" / "gen" / "openapi.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False))
    (r / "README.md").write_text("Run it with docker compose -f scripts/compose.yaml up.\n")
    (r / "server" / "ai.go").write_text('key := os.Getenv("AI_PROVIDER_KEY")\n')
    drf._git(r, "init", "-q")
    drf._git(r, "add", ".")
    drf._git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    drf._git(r, "tag", "1.4.0")
    return r


def server():
    users, notes, tokens = {}, {}, set()

    def handler(req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, dict(req.url.params)
        auth = req.headers.get("authorization", "")
        authed = auth.startswith("Bearer ") and auth[7:] in tokens
        if path == "/healthz":
            return httpx.Response(200, text="ready")
        if not path.startswith("/api/"):
            return httpx.Response(200, text="<!doctype html><title>app</title>")
        if path == "/api/instance/profile":
            return httpx.Response(200, json={"version": "1.4.0"})
        if path == "/api/ai/models":
            return ERR(13, "internal error", 500)
        if path == "/api/users" and req.method == "POST":
            body = json.loads(req.content)
            if users and not authed:
                return ERR(7, "sign-up is closed", 403)
            users[body["username"]] = body["password"]
            return httpx.Response(200, json={"name": f"users/{body['username']}",
                                             "username": body["username"], "password": "",
                                             "createTime": "2026-11-07T05:22:21Z"})  # fmt: skip
        if path == "/api/auth/signin":
            c = json.loads(req.content).get("passwordCredentials") or {}
            if users.get(c.get("username")) != c.get("password") or not users:
                return ERR(16, "unauthenticated", 401)
            tok = jwt(c["username"])
            tokens.add(tok)
            return httpx.Response(200, json={"accessToken": tok, "user": {}})
        if not authed:
            return ERR(16, "authentication required", 401)
        if path == "/api/notes" and req.method == "POST":
            i = f"n{len(notes) + 1:04d}x"
            notes[i] = {"name": f"notes/{i}", "creator": "users/tpadmin",
                        **json.loads(req.content)}  # fmt: skip
            return httpx.Response(200, json=notes[i])
        if path == "/api/notes":
            rows = list(notes.values())
            start = int(base64.b64decode(q["pageToken"])) if q.get("pageToken") else 0
            size = int(q.get("pageSize") or 50)
            page = rows[start : start + size]
            nxt = (
                base64.b64encode(str(start + size).encode()).decode()
                if start + size < len(rows)
                else ""
            )
            return httpx.Response(200, json={"notes": page, "nextPageToken": nxt})
        if path.startswith("/api/notes/"):
            i = path.rsplit("/", 1)[-1]
            if i not in notes:
                return ERR(5, "note not found", 404)
            if req.method == "DELETE":
                del notes[i]
                return httpx.Response(200, json={})
            return httpx.Response(200, json=notes[i])
        if path == "/api/stats":
            return httpx.Response(200, json={"notes": len(notes)})
        return ERR(5, "Not Found", 404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")


class FakeReview:
    def __init__(self):
        self.list = []

    def create(self, kind, payload):
        self.list.append({"id": len(self.list) + 1, "kind": kind, "payload": payload,
                          "status": "open", "decision": None, "detail": None})  # fmt: skip
        return len(self.list)

    def items(self):
        return self.list

    def answer(self, item_id, text):
        self.list[item_id - 1] |= {"status": "resolved", "decision": "answer",
                                   "detail": {"text": text}}  # fmt: skip


def model(prefix_calls: list):
    def provider(prompt, model, params):
        if "Which files" in prompt:
            return json.dumps({"files": ["README.md"]}), {}
        if "Authorization header" in prompt:
            prefix_calls.append(prompt)
            return json.dumps({"prefixes": ["Bearer"]}), {}
        return json.dumps({"candidates": []}), {}  # no admin env: the first user is created

    return provider


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(bb.Stack, "client", server())
    # every bring-up starts from an empty database, like a fresh container
    monkeypatch.setattr(bb.Stack, "start", lambda self, health=None, wait=True:
                        setattr(bb.Stack, "client", server()))  # fmt: skip
    monkeypatch.setattr(bb.Stack, "down", lambda self: None)
    monkeypatch.setattr(bb.Stack, "logs", lambda self: "level=ERROR msg=AI_PROVIDER_KEY is not set")


def run(repo, *, review=None, mode="record", provider=None, nc=False, prefix_calls=None):
    opts = artifacts.put(
        "run_options", {"target": str(repo)} | ({"no_conventions": True} if nc else {})
    )
    gw = llm.Gateway(mode, provider or model(prefix_calls if prefix_calls is not None else []))
    ctx = StageContext(llm=gw, review=review, env=None, emit=lambda *a, **k: None)
    return Discover().run(ctx, {"run_options": opts})


EXPECTED = {
    "target": {"name": SLUG},
    "reach": {"value": {"strategy": "container", "ref": "1.4.0", "compose": "scripts/compose.yaml",
                        "service": "app", "port": 5230, "image_tag_from": "ref",
                        "health": "/healthz", "env": {}}},
    "spec": {"value": {"strategy": "repo_file", "openapi": "proto/gen/openapi.yaml", "base": "/api"}},
    "auth": {"value": {"strategy": "token_header", "scheme": "Bearer", "check": "/api/notes",
                       "login": {"path": "/api/auth/signin", "token_field": "accessToken", "json": True,
                                 "body": {"passwordCredentials": {"username": "x",
                                                                  "password": {"secret": "p"}}}},
                       "bootstrap": {"path": "/api/users", "json": True, "body": {
                           "password": {"secret": "admin_password"}, "username": "tpadmin",
                           "role": "ADMIN", "state": "NORMAL"}},
                       "secured": {"default": "secured",
                                   "public": ["POST /api/auth/signin", "POST /api/users"],
                                   "observed_open": ["GET /api/instance/profile"]}}},
    "data": {"value": {"strategy": "spec_derived", "read_back_timeout_s": 120, "id_field": "name",
                       "id_style": "string",
                       "paging": {"style": "token", "size": "pageSize", "token": "pageToken"}}},
    "state": {"value": {"strategy": "namespace"}},
    "mutation": {"value": {"strategy": "none"}},
    "safety": {"value": {"strategy": "unrestricted"}},
    "checks": {"value": {"strategy": "pytest_cases", "families": {
        "per_family": 5, "examples": 10, "rate": 5,
        "exclude": [{"op": "GET /api/ai/models",
                     "reason": "needs_dependency: AI_PROVIDER_KEY is not set"}]}}},
}  # fmt: skip


def _expected(repo, prof):
    return EXPECTED | {"target": {"name": SLUG, "repo": str(repo)},
                       "commit_tested": prof["commit_tested"]}  # fmt: skip


def test_names_tokens_and_a_first_user_are_discovered(fake, repo):
    review = FakeReview()
    res = run(repo, review=review)
    assert res.status == "succeeded", res.reason
    prof = res.outputs["target_profile"]
    assert compare.diff(prof, _expected(repo, prof)) == []
    a = prof["auth"]["value"]
    assert a["bootstrap"] == EXPECTED["auth"]["value"]["bootstrap"]
    assert a["secured"] == EXPECTED["auth"]["value"]["secured"]
    assert prof["checks"]["value"] == EXPECTED["checks"]["value"]
    assert prof["spec"]["provenance"]["openapi"]["layer"] == "algorithm"  # found, not listed
    assert prof["spec"]["provenance"]["openapi"]["probe"] is not None  # the spec-level check
    (item,) = review.list
    assert item["kind"] == "confirm_observed" and item["payload"]["about"] == "secured"
    assert item["payload"]["ops"] == [{"op": "GET /api/instance/profile", "description": "Public."}]


def test_replay_is_byte_identical(fake, repo, monkeypatch):
    assert run(repo).status == "succeeded"
    first = files(SLUG)[0].read_bytes()
    monkeypatch.setattr(
        bb.Stack,
        "client",
        httpx.Client(
            transport=httpx.MockTransport(lambda r: pytest.fail(f"replay made a request: {r.url}")),
            base_url="http://t",
        ),
    )
    assert run(repo, mode="replay", provider=llm._no_provider).status == "succeeded"
    assert files(SLUG)[0].read_bytes() == first


def test_no_conventions_finds_the_same_fields_by_exploration(fake, repo):
    normal = run(repo).outputs["target_profile"]
    calls = []
    res = run(repo, nc=True, prefix_calls=calls)
    assert res.status == "succeeded", res.reason
    nc = res.outputs["target_profile"]
    assert files(SLUG, True)[0].exists() and files(SLUG)[0].exists()  # side by side (D4)
    assert len(calls) == 1  # the header prefix came from the model
    assert nc["auth"]["provenance"]["scheme"]["layer"] == "exploration"
    assert nc["data"]["provenance"]["paging"]["layer"] == "algorithm"
    assert nc["reach"]["value"]["health"] == "/"  # no health_path list: the app root
    diffs = compare.diff(nc, normal)
    assert diffs == ["reach.health: discovered '/', reference '/healthz'"]


def test_a_setup_answer_is_verified_and_recorded_as_clarify(monkeypatch, tmp_path):
    """The DRF-like fake whose docs the model can't read: discovery asks for the admin env."""
    r = drf.make_repo(tmp_path)
    drf_up(monkeypatch)
    review = FakeReview()
    blind = lambda prompt, model, params: (json.dumps({"files": []}) if "Which files" in prompt
                                           else json.dumps({"candidates": []}))  # fmt: skip
    res = run(r, review=review, provider=blind)
    assert res.status == "awaiting_review"
    (q,) = review.list
    assert q["kind"] == "setup" and q["payload"]["key"] == "admin_env"
    review.answer(q["id"], "THING_ADMIN_USER THING_ADMIN_PASSWORD")
    res = run(r, review=review, provider=blind)
    assert res.status == "succeeded", res.reason
    prof = res.outputs["target_profile"]
    assert prof["reach"]["provenance"]["env"]["source"] == "clarify"
    assert prof["reach"]["value"]["env"]["THING_ADMIN_USER"] == "tpadmin"


def test_a_wrong_setup_answer_leads_to_the_next_question_then_fails(monkeypatch, tmp_path):
    r = drf.make_repo(tmp_path)
    drf_up(monkeypatch)
    review = FakeReview()
    blind = lambda prompt, model, params: json.dumps({"files": [], "candidates": []})
    assert run(r, review=review, provider=blind).status == "awaiting_review"
    review.answer(1, "WRONG_USER WRONG_PASSWORD")  # verified by the login probe: it fails
    assert run(r, review=review, provider=blind).status == "awaiting_review"
    assert review.list[1]["payload"]["key"] == "login"  # the next unknown, never the same again
    review.answer(2, "/api/token/")
    res = run(r, review=review, provider=blind)
    assert res.status == "failed" and len(review.list) == 2


def drf_up(monkeypatch):
    """The DRF-like fake, started with the profile's env: its admin exists only when the env
    names the admin vars (as the real image does at first start)."""

    def start(self, health=None, wait=True):
        env = self.r.get("env") or {}
        bb.Stack.client = drf.fake_server("thing-server", admin="THING_ADMIN_USER" in env)

    monkeypatch.setattr(bb.Stack, "client", None)
    monkeypatch.setattr(bb.Stack, "start", start)
    monkeypatch.setattr(bb.Stack, "down", lambda self: None)


def test_secrets_never_reach_the_profile_or_the_log(fake, repo, data):
    assert run(repo).status == "succeeded"
    assert secrets.leaks(data / "targets") == []
    text = files(SLUG)[1].read_text()
    assert secrets.get(SLUG, "admin_password") not in text


def test_the_targets_page_lists_fields_counts_and_the_comparison(fake, repo):
    from tp.stages.discover import targets

    assert run(repo).status == "succeeded"
    assert run(repo, nc=True).status == "succeeded"
    (t,) = targets.list_()
    assert t["slug"] == SLUG and t["no_conventions"] and t["counts"]["override"] == 0
    d = targets.detail(SLUG)
    keys = {(f["concern"], f["key"]) for f in d["fields"]}
    assert {("auth", "bootstrap"), ("data", "id_field"), ("spec", "openapi")} <= keys
    assert d["excluded"][0]["op"] == "GET /api/ai/models"
    c = d["compare"]
    assert c["same_commit"]
    differ = {(r["concern"], r["key"]) for r in c["rows"] if not r["same_value"]}
    assert differ == {("reach", "health")}
    scheme = next(r for r in c["rows"] if (r["concern"], r["key"]) == ("auth", "scheme"))
    assert (
        scheme["normal"]["layer"] == "convention"
        and scheme["no_conventions"]["layer"] == "exploration"
    )


# ---- discover/006: fixes found by the independent review ----------------------------------------


def test_a_repo_spec_is_not_confirmed_by_refusals_alone(monkeypatch):
    """An auth gate answers 401 for any path, real or missing: refusals can't confirm a repo spec.
    Before 006 every anonymous 401 counted as "differs from the missing route's 404"."""
    from tp.stages.discover import discovery

    d = discovery.Discovery(f"https://example.test/{SLUG}", "1.0.0", None, discovery.Probes())
    d.spec = {"paths": {p: {"get": {}} for p in ("/api/a", "/api/b", "/api/c")}}
    d.values = {"spec": {"strategy": "repo_file", "openapi": "x.yaml", "base": "/api"},
                "auth": {"strategy": "token_header", "scheme": "Bearer"}}  # fmt: skip
    d.public, d.prov = [], {"spec": {"openapi": {"probe": None}}}
    monkeypatch.setattr(d, "auth_headers", lambda: {"Authorization": "Bearer t"})

    def http(method, path, headers=None, **kw):
        if headers:  # authenticated: the missing route's answer
            return 0, 404, {"code": 5, "message": "Not Found", "details": []}
        return 0, 401, {"code": 16, "message": "authentication required", "details": []}

    monkeypatch.setattr(d, "http", http)
    d.step_secured()
    assert d.values["auth"]["secured"]["default"] == "secured"
    with pytest.raises(discovery.DiscoveryError, match="0/0 answers differ"):
        d.step_spec_check()


def test_discovery_waits_while_a_run_uses_the_targets_stack(fake, repo, data):
    """Discovery shares the compose project, clone and token with a run on the same target."""
    import shutil
    from pathlib import Path

    from tp.core import runner

    dest = data / "targets" / SLUG
    shutil.copytree(Path("tests/fixtures/targets/memos"), dest)
    rid = runner.start_run("live", options={"profile": str(dest)})
    assert runner.run_target(rid) == str(dest.resolve())
    runner._set_run(rid, "running")
    res = run(repo)
    assert res.status == "failed" and f"r-{rid:04d} is using {SLUG}" in res.reason
    runner._set_run(rid, "succeeded")
    assert run(repo).status == "succeeded"


def test_the_same_open_operations_are_asked_about_once(fake, repo):
    from tp.core import review

    first = FakeReview()
    assert run(repo, review=first).status == "succeeded"
    (item,) = first.list
    review.resolve(review.create(1, item["kind"], item["payload"]), "confirm", by="test")
    again = FakeReview()
    assert run(repo, review=again).status == "succeeded"
    assert again.list == []  # already answered for exactly these operations


def test_a_replay_needs_the_clone_at_the_ref_and_never_fetches(fake, repo, data):
    import shutil

    assert run(repo).status == "succeeded"
    shutil.rmtree(data / "sut" / SLUG)
    res = run(repo, mode="replay", provider=llm._no_provider)
    assert res.status == "failed" and "replay needs" in res.reason
    assert json.loads(files(SLUG)[1].read_text())["commit"]  # live logs record the commit
