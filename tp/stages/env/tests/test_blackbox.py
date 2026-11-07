"""Contract tests for the black-box strategies (blackbox D12): scripted HTTP for the HTTP logic,
pure YAML for the compose rewrite. The Docker and Paperless halves are in test_blackbox_docker."""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from tp.core import profile, secrets, strategies
from tp.stages.env import blackbox as bb

FIXTURE = Path(__file__).parents[4] / "tests" / "fixtures" / "targets" / "paperless-ngx"
COMPOSE = FIXTURE / "compose" / "docker-compose.sqlite.yml"


@pytest.fixture
def paperless(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TP_TARGET", str(FIXTURE))
    bb._STACKS.clear()
    return profile.load()


def _rewrite(p, tmp_path, compose=None):
    raw = compose or yaml.safe_load(COMPOSE.read_text())
    return bb.rewrite(raw, COMPOSE.parent, tmp_path / "env", p.reach.value, {})


def test_tp_target_path_selects_a_fixture_folder_and_resolves_black_box_strategies(paperless):
    assert (paperless.slug, paperless.dir) == ("paperless-ngx", FIXTURE)
    s = strategies.resolve(paperless)
    assert isinstance(s.reach, bb.Reach) and isinstance(s.auth, bb.TokenHeaderAuth)
    assert isinstance(s.state, bb.NamespaceState) and not s.mutation.available()
    assert s.reach.image() is None and s.reach.exec(["anything"]) is None
    assert s.reach.stack is s.spec.stack is s.checks.stack  # one stack per target


def test_the_template_still_resolves_its_own_strategies(monkeypatch):
    monkeypatch.delenv("TP_TARGET", raising=False)
    from tp.stages.env.strategies import ContainerReach

    assert isinstance(strategies.resolve(profile.load()).reach, ContainerReach)


def test_rewrite_sandboxes_paperless_compose(paperless, tmp_path):
    out, refs = _rewrite(paperless, tmp_path)
    web, broker = out["services"]["webserver"], out["services"]["broker"]
    assert "ports" not in web and "restart" not in web and "restart" not in broker
    assert web["image"] == "ghcr.io/paperless-ngx/paperless-ngx:v3.4.2"  # tag-matched (D4)
    assert broker["image"] == "docker.io/valkey/valkey:9-alpine"  # pinned tags stay
    binds = [v for v in web["volumes"] if v.startswith("/")]
    assert len(binds) == 2 and all(v.startswith(str(tmp_path / "env" / "binds")) for v in binds)
    assert "data:/usr/src/paperless/data" in web["volumes"]  # named volumes: project-scoped
    env = web["environment"]
    assert env["PAPERLESS_DBENGINE"] == "sqlite"  # the compose's own environment
    assert env["PAPERLESS_SECRET_KEY"] == "${TP_SECRET_APP_SECRET_KEY}"  # profile beats env_file
    assert env["PAPERLESS_ADMIN_PASSWORD"] == "${TP_SECRET_ADMIN_PASSWORD}"  # never the value
    assert refs == {"admin_password", "app_secret_key"}
    assert out["networks"]["run"] == {"internal": True}
    assert all(s["networks"] == ["run"] for n, s in out["services"].items() if n != bb.FORWARDER)
    fwd = out["services"][bb.FORWARDER]
    assert fwd["networks"] == ["run", "edge"] and fwd["ports"] == ["127.0.0.1::8000"]
    assert fwd["command"][-1] == "TCP:webserver:8000"
    assert web["mem_limit"] and web["cpus"]


@pytest.mark.parametrize("key", ["privileged_x", "pid_mode", "volumes_from", "configs"])
def test_rewrite_fails_closed_on_unknown_service_keys(paperless, tmp_path, key):
    raw = yaml.safe_load(COMPOSE.read_text())
    raw["services"]["webserver"][key] = "x"
    with pytest.raises(bb.BlackboxError, match="unsupported keys"):
        _rewrite(paperless, tmp_path, raw)


def test_rewrite_builds_at_the_commit_and_escapes_dollars(paperless, tmp_path):
    raw = {"services": {"webserver": {"build": "..", "environment": ["A=x$y"]}}}
    out, _ = bb.rewrite(raw, COMPOSE.parent, tmp_path / "env", paperless.reach.value,
                        {"webserver": "tp-x-webserver:abc"}, repo=COMPOSE.parent.parent)  # fmt: skip
    web = out["services"]["webserver"]
    assert web["image"] == "tp-x-webserver:abc"
    assert web["build"]["context"] == str(COMPOSE.parent.parent.resolve())
    assert web["environment"]["A"] == "x$y"  # the repo's own value: compose interpolates it


@pytest.mark.parametrize(
    ("build", "match"),
    [({"context": ".", "ssh": ["default"]}, "unsupported build keys"),
     ({"context": ".", "additional_contexts": {"x": "/etc"}}, "unsupported build keys"),
     ({"context": "../../.."}, "outside the repo")],
)  # fmt: skip
def test_builds_that_could_reach_the_host_fail_closed(paperless, tmp_path, build, match):
    raw = {"services": {"webserver": {"build": build}}}
    with pytest.raises(bb.BlackboxError, match=match):
        bb.rewrite(raw, COMPOSE.parent, tmp_path / "env", paperless.reach.value,
                   {"webserver": "x:y"}, repo=COMPOSE.parent.parent)  # fmt: skip


@pytest.mark.parametrize(
    ("image", "want"),
    [("a/b:latest", "a/b:v1"), ("a/b", "a/b:v1"), ("a/b:9-alpine", "a/b:9-alpine"),
     ("host:5000/b", "host:5000/b:v1")],
)  # fmt: skip
def test_retag(image, want):
    assert bb.retag(image, "v1") == want


def _scripted(stack, handler):
    stack.client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")


def test_token_login_is_fetched_once_and_kept_as_a_secret(paperless):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        assert b"password=" in req.content and b"username=tpadmin" in req.content
        return httpx.Response(200, json={"token": "t0k3n-abcdefgh"})

    stack = bb.Stack(paperless)
    _scripted(stack, handler)
    auth = bb.TokenHeaderAuth(paperless, stack)
    assert auth.principal("admin") == {"scheme": "Token", "token": "t0k3n-abcdefgh"}
    auth.principal("admin")
    assert calls == ["/api/token/"]
    assert secrets.load("paperless-ngx")["token"] == "t0k3n-abcdefgh"


@pytest.mark.parametrize("reply", [httpx.Response(400, json={}), httpx.Response(200, json={})])
def test_a_login_without_a_token_fails_loudly(paperless, reply):
    stack = bb.Stack(paperless)
    _scripted(stack, lambda req: reply)
    with pytest.raises(bb.BlackboxError, match="login /api/token/"):
        bb.TokenHeaderAuth(paperless, stack).principal("admin")


def test_basic_auth_encodes_the_pair_and_stores_it_for_redaction(paperless):
    from dataclasses import replace

    p = replace(paperless, fields=paperless.fields | {"auth": profile.Field(
        {"strategy": "basic", "user": "tpadmin", "password": {"secret": "admin_password"}},
        "override")})  # fmt: skip
    who = bb.BasicAuth(p).principal("admin")
    pw = secrets.get("paperless-ngx", "admin_password")
    import base64

    assert base64.b64decode(who["token"]).decode() == f"tpadmin:{pw}" and who["scheme"] == "Basic"
    assert secrets.redact(f"Authorization: Basic {who['token']}") == (
        "Authorization: Basic $TP_SECRET_BASIC"
    )


def test_auth_probe_needs_401_anonymous_and_200_with_the_principal(paperless):
    secrets.put("paperless-ngx", "token", "t0k3n-abcdefgh")

    def handler(req):
        ok = req.headers.get("authorization") == "Token t0k3n-abcdefgh"
        return httpx.Response(200 if ok else 401, json={})

    stack = bb.Stack(paperless)
    _scripted(stack, handler)
    probes = {p["name"]: p for p in stack.probe()}
    assert probes["auth"]["ok"] and probes["health"]["ok"] is False  # health has no auth here
    _scripted(stack, lambda req: httpx.Response(200, json={}))  # an API that ignores auth
    assert not {p["name"]: p for p in stack.probe()}["auth"]["ok"]


SPEC = {"paths": {"/api/tags/": {"get": {}, "post": {}}, "/api/tags/{id}/": {"delete": {}},
                  "/api/tasks/": {"get": {}}}}  # fmt: skip


@pytest.mark.parametrize(
    ("entry", "want"),
    [({"path": "/api/tags/", "id": 7, "location": None}, "/api/tags/7/"),
     ({"path": "/api/tasks/", "id": 1, "location": None}, None),
     ({"path": "/x/", "id": None, "location": "http://h/api/tags/9/"}, "/api/tags/9/")],
)  # fmt: skip
def test_delete_path_from_the_spec(entry, want):
    assert bb.delete_path(SPEC, entry) == want


def test_cleanup_deletes_newest_first_and_lists_leftovers(paperless, tmp_path):
    secrets.put("paperless-ngx", "token", "t0k3n-abcdefgh")
    stack = bb.Stack(paperless)
    stack.dir.mkdir(parents=True)
    (stack.dir / "openapi.json").write_text(json.dumps(SPEC))
    deleted = []

    def handler(req):
        deleted.append(req.url.path)
        return httpx.Response(500 if req.url.path == "/api/tags/8/" else 204)

    _scripted(stack, handler)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(e) + "\n" for e in [
        {"path": "/api/tags/", "id": 7, "location": None},
        {"path": "/api/tags/", "id": 8, "location": None},
        {"path": "/api/tasks/", "id": 1, "location": None},
        {"path": "/api/tags/", "id": 9, "location": None},
        {"path": "/api/tags/", "id": 9, "location": None, "deleted_by_test": True}]))  # fmt: skip
    out = stack.cleanup(ledger)
    assert deleted == ["/api/tags/8/", "/api/tags/7/"]
    assert [e["id"] for e in out["deleted"]] == [7] and [e["id"] for e in out["failed"]] == [8]
    assert [e["path"] for e in out["unknown_delete_route"]] == ["/api/tasks/"]
    assert [e["id"] for e in out["deleted_by_test"]] == [9]  # not deleted twice
