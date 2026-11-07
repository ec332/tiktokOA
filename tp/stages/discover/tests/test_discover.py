"""Discover stage contract tests: a scripted fake (a tiny git repo + an HTTP fake with DRF-like
conventions), a scripted model, record then replay with no transport (discover/001 D2)."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from tp.core import artifacts, llm, runner, secrets
from tp.core.contract import StageContext
from tp.stages.discover import compare, discovery
from tp.stages.discover.stage import Discover, target_dir
from tp.stages.env import blackbox as bb

FIXTURE = Path(__file__).parents[4] / "tests" / "fixtures" / "targets" / "paperless-ngx"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def make_repo(tmp_path):
    r = tmp_path / "upstream" / "thing-server"
    (r / "deploy").mkdir(parents=True)
    (r / "docs").mkdir()
    big = {"services": {"app": {"image": "example/thing:latest", "ports": ["8000:8000"],
                                "env_file": "app.env"},
                        "cache": {"image": "redis:7"}, "db": {"image": "postgres:16"}}}  # fmt: skip
    small = {"services": {"app": {"image": "example/thing:latest", "ports": ["8000:8000"],
                                  "env_file": "app.env"}, "cache": {"image": "redis:7"}}}  # fmt: skip
    (r / "deploy" / "docker-compose.full.yml").write_text(yaml.safe_dump(big))
    (r / "deploy" / "docker-compose.lite.yml").write_text(yaml.safe_dump(small))
    (r / "deploy" / "app.env").write_text("THING_SECRET_KEY=change-me\nTHING_DEBUG=false\n")
    (r / "docs" / "setup.md").write_text(
        "Set THING_ADMIN_USER and THING_ADMIN_PASSWORD to create an admin at first start.\n"
    )
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    _git(r, "tag", "v1.2.0")
    _git(r, "tag", "v1.10.0")  # numeric, not lexical, order picks this one
    _git(r, "tag", "v2.0.0-rc.1")  # a pre-release is never the default
    return r


SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/api/config/": {"get": {"security": [{"tokenAuth": []}]}},
        "/api/token/": {"post": {
            "requestBody": {"content": {"application/x-www-form-urlencoded": {
                "schema": {"$ref": "#/components/schemas/Login"}}}},
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "object", "properties": {"token": {"type": "string"}}}}}}}}},
        "/api/things/": {
            "get": {"security": [{"tokenAuth": []}], "parameters": [
                {"name": "id", "in": "query", "schema": {"type": "integer"}},
                {"name": "page", "in": "query", "schema": {"type": "integer"}},
                {"name": "page_size", "in": "query", "schema": {"type": "integer"}}]},
            "post": {"security": [{"tokenAuth": []}], "requestBody": {"content": {
                "application/json": {"schema": {"type": "object", "required": ["name"],
                                                "properties": {"name": {"type": "string"}}}}}}}},
        "/api/things/{id}/": {"get": {}, "delete": {}},
    },
    "components": {"schemas": {"Login": {"type": "object", "required": ["username", "password"],
                                         "properties": {"username": {"type": "string"},
                                                        "password": {"type": "string",
                                                                     "writeOnly": True}}}},
                   "securitySchemes": {"tokenAuth": {"type": "apiKey", "in": "header",
                                                     "name": "Authorization"}}},
}  # fmt: skip


def fake_server(slug, admin=True):
    things, token = {}, "tok-0123456789abcdef"

    def handler(req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, dict(req.url.params)
        authed = req.headers.get("authorization") == f"Token {token}"
        if path == "/":
            return httpx.Response(302, headers={"location": "/login"})
        if path == "/api/schema/" and q.get("format") == "json":
            return httpx.Response(200, json=SPEC)
        if path == "/api/token/" and req.method == "POST":
            form = dict(httpx.QueryParams(req.content.decode()))
            ok = admin and form == {
                "username": "tpadmin",
                "password": secrets.get(slug, "admin_password"),
            }  # the env var at start
            return httpx.Response(200, json={"token": token}) if ok else httpx.Response(400)
        if not authed:
            return httpx.Response(
                401, json={"detail": "Authentication credentials were not provided."}
            )
        if path == "/api/config/":
            return httpx.Response(200, json=[])
        if path == "/api/things/" and req.method == "POST":
            i = max(things, default=0) + 1
            things[i] = {"id": i, "name": json.loads(req.content)["name"], "slug": f"t{i}"}
            return httpx.Response(201, json=things[i])
        if path == "/api/things/":
            size, page = int(q.get("page_size", 25)), int(q.get("page", 1))
            rows = list(things.values())[(page - 1) * size : page * size]
            return httpx.Response(200, json={"count": len(things), "results": rows})
        if path.startswith("/api/things/"):
            i = int(path.strip("/").rsplit("/", 1)[-1]) if path.strip("/")[-1].isdigit() else -1
            if i not in things:
                return httpx.Response(404, json={"detail": "Not found."})
            if req.method == "DELETE":
                del things[i]
                return httpx.Response(204)
            return httpx.Response(200, json=things[i])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")


def scripted(prompt, model, params):
    if "Which files" in prompt:
        return json.dumps({"files": ["docs/setup.md"]}), {"input_tokens": 10, "output_tokens": 5}
    assert "THING_ADMIN_USER" in prompt  # the picked file was read into the prompt
    return json.dumps({"candidates": [{"user_env": "THING_ADMIN_USER",
                                       "password_env": "THING_ADMIN_PASSWORD",
                                       "file": "docs/setup.md", "quote": "Set THING_ADMIN_USER"}]}), {}  # fmt: skip


def _ctx(mode, provider=None):
    gw = llm.Gateway(mode, provider or llm._no_provider)
    return StageContext(llm=gw, review=None, env=None, emit=lambda *a, **k: None)


def _discover(repo, mode="record", provider=scripted, target=None):
    opts = artifacts.put("run_options", {"target": target or str(repo)})
    return Discover().run(_ctx(mode, provider), {"run_options": opts})


@pytest.fixture
def fake(monkeypatch, repo):
    ups = []
    monkeypatch.setattr(
        bb.Stack, "start", lambda self, health=None, wait=True: ups.append(self.r["compose"])
    )
    monkeypatch.setattr(bb.Stack, "down", lambda self: None)
    monkeypatch.setattr(bb.Stack, "client", fake_server("thing-server"))
    return ups


REFERENCE = {
    "target": {"name": "thing-server"},
    "reach": {"value": {"strategy": "container", "ref": "v1.10.0",
                        "compose": "deploy/docker-compose.lite.yml", "service": "app",
                        "port": 8000, "image_tag_from": "ref", "health": "/api/schema/",
                        "env": {"THING_ADMIN_USER": "admin",
                                "THING_ADMIN_PASSWORD": {"secret": "pw"},
                                "THING_SECRET_KEY": {"secret": "k"}}}},
    "spec": {"value": {"strategy": "served", "openapi": "/api/schema/", "base": "/api"}},
    "auth": {"value": {"strategy": "token_header", "scheme": "Token", "check": "/api/x/",
                       "login": {"path": "/api/token/", "token_field": "token",
                                 "body": {"username": "admin", "password": {"secret": "pw"}}}}},
    "data": {"value": {"strategy": "spec_derived", "read_back_timeout_s": 120, "id_field": "id",
                       "id_style": "integer",
                       "paging": {"style": "page", "page": "page", "size": "page_size"}}},
    "state": {"value": {"strategy": "namespace"}},
    "mutation": {"value": {"strategy": "none"}},
    "safety": {"value": {"strategy": "unrestricted"}},
    "checks": {"value": {"strategy": "pytest_cases",
                         "families": {"per_family": 5, "examples": 10, "rate": 5, "exclude": []}}},
}  # fmt: skip


def test_discovers_a_drf_like_target_and_matches_the_reference(fake, repo):
    res = _discover(repo)
    assert res.status == "succeeded", res.reason
    prof = res.outputs["target_profile"]
    ref = REFERENCE | {"target": {"name": "thing-server", "repo": str(repo)},
                       "commit_tested": prof["commit_tested"]}  # fmt: skip
    assert compare.diff(prof, ref) == []
    assert fake == ["deploy/docker-compose.lite.yml"]  # fewest services first, came up first time
    assert prof["reach"]["provenance"]["env"]["source"] == "ai_proposed+verified"
    assert prof["reach"]["source"] == "ai_proposed+verified"  # the weakest sub-key
    assert prof["spec"]["provenance"]["openapi"]["layer"] == "convention"
    assert prof["data"]["provenance"]["id_field"] == {"source": "probed", "layer": "algorithm",
                                                      "probe": prof["data"]["provenance"]["id_field"]["probe"]}  # fmt: skip
    # the profile loads, and the env strategies resolve from it
    from tp.core import profile, strategies

    p = profile.parse(yaml.safe_load((target_dir("thing-server") / "profile.yaml").read_text()),
                      "thing-server")  # fmt: skip
    assert strategies.resolve(p).auth is not None


def test_replay_needs_no_transport_and_gives_the_same_profile_bytes(fake, repo, monkeypatch):
    assert _discover(repo).status == "succeeded"
    first = (target_dir("thing-server") / "profile.yaml").read_bytes()

    def boom(req):
        raise AssertionError(f"replay made a request: {req.url}")

    monkeypatch.setattr(bb.Stack, "client", httpx.Client(transport=httpx.MockTransport(boom),
                                                         base_url="http://t"))  # fmt: skip
    monkeypatch.setattr(bb.Stack, "start", lambda *a, **k: pytest.fail("replay started Docker"))
    kept = secrets.load("thing-server")
    res = _discover(repo, mode="replay", provider=None)
    assert res.status == "succeeded", res.reason
    assert (target_dir("thing-server") / "profile.yaml").read_bytes() == first
    assert secrets.load("thing-server") == kept  # a replay never stores the redacted placeholder


def test_the_probe_log_holds_no_secret(fake, repo, data):
    assert _discover(repo).status == "succeeded"
    log = (target_dir("thing-server") / "probes.json").read_text()
    assert secrets.get("thing-server", "admin_password") not in log
    assert "tok-0123456789abcdef" not in log
    assert secrets.leaks(data / "targets") == []


def test_an_explicit_ref_wins_and_a_failure_is_logged(fake, repo, data, monkeypatch):
    monkeypatch.setattr(
        bb.Stack,
        "client",
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404)), base_url="http://t"
        ),
    )
    res = _discover(repo, target=f"{repo}@v1.2.0")
    assert res.status == "failed" and "no served spec" in res.reason
    entry = json.loads((data / "discovery" / "failures.jsonl").read_text().splitlines()[-1])
    assert entry["target"] == "thing-server" and entry["class"] is None


def test_id_detection_takes_the_last_segment_of_a_resource_name():
    """P2: `name: things/abc` while the item path is `/api/things/{thing}`."""
    d = discovery.Discovery("https://example.invalid/x/y", "v1", None, discovery.Probes())
    seen = []

    class S:
        def http(self, m, path, **kw):
            seen.append(path)
            return (200, {}) if path == "/api/things/abc" else (404, {})

    d.stack = S()
    got = d.id_field("/api/things/{thing}", {"name": "things/abc", "title": "x", "n": 3}, {})
    assert got[:3] == ("name", "string", "suffix")
    assert seen == ["/api/things/things/abc", "/api/things/x", "/api/things/3", "/api/things/abc"]


def test_common_base():
    assert discovery.common_base(["/api/a/", "/api/b/{id}/"]) == "/api"
    assert discovery.common_base(["/api/v1/memos", "/api/v1/users:batchGet"]) == "/api/v1"
    assert discovery.common_base(["/a", "/b"]) == ""


def test_the_comparator_on_the_paperless_fixture():
    ref = yaml.safe_load((FIXTURE / "profile.yaml").read_text())
    assert compare.diff(ref, ref) == []
    found = yaml.safe_load((FIXTURE / "profile.yaml").read_text())
    found["spec"]["value"]["openapi"] = "/api/schema/"  # YAML vs JSON: the same spec
    found["auth"]["value"]["login"]["body"]["username"] = "someone"  # user names may differ
    found["auth"]["value"]["check"] = "/api/config/"
    found["data"]["value"]["id_field"] = "pk"
    assert compare.diff(found, ref) == ["data.id_field: discovered 'pk', reference 'id'"]


def test_a_run_can_hold_only_some_stages():
    run_id = runner.start_run("live", stub=True, pipeline=["spec"])
    run = runner.advance(run_id)
    assert runner.stages(run_id) == ["spec"]
    assert [e["stage"] for e in run["executions"] if e["stage"] != "input"] == ["spec"]
    assert run["status"] == "succeeded"
