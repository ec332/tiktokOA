"""Env strategies for discovered profiles (discover/002): floating tags, nested secret bodies, the
replayed first-user bootstrap, JSON logins, repo-file specs, and the secured map with its
`confirm_observed` answer."""

import json
import subprocess

import httpx
import pytest
import yaml

from tp.core import profile, review, secrets
from tp.stages.env import blackbox as bb


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    bb._STACKS.clear()


def prof(tmp_path, auth=None, spec=None, slug="notes"):
    raw = {"target": {"name": slug, "repo": str(tmp_path / "repo")}, "commit_tested": "x",
           "reach": {"source": "probed", "value": {"strategy": "container", "ref": "1.0.0",
                                                    "service": "app", "port": 5230,
                                                    "health": "/healthz"}},
           "spec": {"source": "repo", "value": spec or {"strategy": "served",
                                                       "openapi": "/openapi.json"}},
           "auth": {"source": "probed", "value": auth or {"strategy": "token_header"}}}  # fmt: skip
    for c in ("data", "state", "mutation", "safety", "checks"):
        raw[c] = {"source": "resolver", "value": {"strategy": "x"}}
    raw["data"]["value"] = {"strategy": "spec_derived"}
    return profile.parse(raw, slug)


@pytest.mark.parametrize(("image", "want"), [
    ("org/app:stable", "org/app:1.0.0"), ("org/app:canary", "org/app:1.0.0"),
    ("org/app:2.3", "org/app:2.3"), ("org/app", "org/app:1.0.0")])  # fmt: skip
def test_floating_tags_are_retagged_and_pinned_ones_kept(image, want):
    assert bb.retag(image, "1.0.0") == want


def test_nested_secret_bodies_resolve_and_become_runner_placeholders():
    body = {"credentials": {"username": "tpadmin", "password": {"secret": "admin_password"}}}
    pw = secrets.get("notes", "admin_password", generate=True)
    assert bb._resolve("notes", body) == {"credentials": {"username": "tpadmin", "password": pw}}
    assert bb._placeholders(body)["credentials"]["password"] == "$TP_SECRET_ADMIN_PASSWORD"


def _client(log, token="jwt-like-token-0001"):
    def handler(req):
        log.append((req.method, req.url.path, req.content.decode()))
        if req.url.path == "/api/auth/signin":
            return httpx.Response(200, json={"accessToken": token})
        return httpx.Response(200, json={"name": "users/tpadmin"})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")


def test_the_bootstrap_is_replayed_then_a_json_login_works(tmp_path):
    auth = {"strategy": "token_header", "scheme": "Bearer", "check": "/api/x",
            "bootstrap": {"path": "/api/users", "json": True,
                          "body": {"username": "tpadmin", "password": {"secret": "admin_password"}}},
            "login": {"path": "/api/auth/signin", "json": True, "token_field": "accessToken",
                      "body": {"c": {"username": "tpadmin",
                                     "password": {"secret": "admin_password"}}}}}  # fmt: skip
    p = prof(tmp_path, auth)
    stack, log = bb.Stack(p), []
    stack.client = _client(log)
    stack.bootstrap()
    who = bb.make_auth(p, stack).principal("admin")
    pw = secrets.get("notes", "admin_password")
    assert log[0][:2] == ("POST", "/api/users")
    assert json.loads(log[0][2]) == {"username": "tpadmin", "password": pw}
    assert json.loads(log[1][2]) == {"c": {"username": "tpadmin", "password": pw}}  # JSON, not form
    assert who == {"scheme": "Bearer", "token": "jwt-like-token-0001"}


def test_an_expired_stored_jwt_is_fetched_again(tmp_path):
    import base64
    import time

    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    old = f"{enc({})}.{enc({'exp': time.time() - 5})}.s"
    secrets.put("notes", "token", old)
    auth = {"strategy": "token_header", "scheme": "Bearer", "check": "/api/x",
            "login": {"path": "/api/auth/signin", "json": True, "token_field": "accessToken",
                      "body": {"u": "tpadmin"}}}  # fmt: skip
    stack, log = bb.Stack(prof(tmp_path, auth)), []
    stack.client = _client(log, token="new-token-000000001")
    assert bb.make_auth(prof(tmp_path, auth), stack).principal()["token"] == "new-token-000000001"


def test_a_repo_file_spec_is_read_at_the_tested_ref(tmp_path):
    repo = tmp_path / "repo"
    (repo / "api").mkdir(parents=True)
    (repo / "api" / "openapi.yaml").write_text(yaml.safe_dump({"openapi": "3.0.3", "paths": {}}))
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t",
                                                 "commit", "-qm", "x"], ["tag", "1.0.0"]):  # fmt: skip
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    stack = bb.Stack(prof(tmp_path, spec={"strategy": "repo_file", "openapi": "api/openapi.yaml"}))
    assert stack.openapi() == {"openapi": "3.0.3", "paths": {}}
    assert json.loads((stack.dir / "openapi.json").read_text())["openapi"] == "3.0.3"


def test_the_secured_map_follows_the_confirm_observed_answer(tmp_path):
    sec = {"default": "secured", "public": ["POST /api/auth/signin"],
           "observed_open": ["GET /api/instance/profile"]}  # fmt: skip
    p = prof(tmp_path, {"strategy": "token_header", "secured": sec})
    assert bb.families_config(p)["secured"] == sec  # unanswered: spec-side until confirmed
    item = review.create(1, "confirm_observed", {"about": "secured", "target": "notes",
                                                 "ops": [{"op": "GET /api/instance/profile"}]})  # fmt: skip
    review.resolve(item, "confirm", by="test")
    got = bb.families_config(p)["secured"]
    assert got["public"] == ["POST /api/auth/signin", "GET /api/instance/profile"]
    assert got["observed_open"] == []
    again = review.create(1, "confirm_observed", {"about": "secured", "target": "notes",
                                                  "ops": [{"op": "GET /api/instance/profile"}]})  # fmt: skip
    review.resolve(again, "reject", by="test")  # the latest answer wins: a hole, so secured
    assert bb.families_config(p)["secured"] == sec | {"observed_open": []}


def test_an_answer_about_other_operations_does_not_carry_over(tmp_path):
    """discover/006: an answer is about one list of operations; a rediscovery that finds a
    different list (or another profile of the slug) asks again instead of reusing it."""
    item = review.create(1, "confirm_observed", {"about": "secured", "target": "notes",
                                                 "ops": [{"op": "GET /api/old"}]})  # fmt: skip
    review.resolve(item, "confirm", by="test")
    sec = {"default": "secured", "public": [], "observed_open": ["GET /api/instance/profile"]}
    p = prof(tmp_path, {"strategy": "token_header", "secured": sec})
    assert bb.families_config(p)["secured"] == sec  # still spec-side: not this list's answer


def test_a_clone_is_moved_to_the_profiles_ref_and_a_dirty_one_refused(tmp_path, monkeypatch):
    """discover/006: an existing clone of another ref is fetched and checked out, never reused."""
    up = tmp_path / "repo"
    up.mkdir()
    git = lambda *a, cwd=up: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                                             *a], cwd=cwd, check=True, capture_output=True)  # fmt: skip
    (up / "f").write_text("1")
    git("init", "-q"), git("add", "."), git("commit", "-qm", "1"), git("tag", "1.0.0")
    (up / "f").write_text("2")
    git("commit", "-qam", "2"), git("tag", "2.0.0")
    p1 = prof(tmp_path)
    src = bb.Stack(p1).src  # cloned at 1.0.0
    assert (src / "f").read_text() == "1"
    raw = {**p1.reach.value, "ref": "2.0.0"}
    p2 = profile.TargetProfile(p1.slug, p1.name, p1.repo, p1.type, p1.commit_tested,
                               p1.fields | {"reach": profile.Field(raw, "probed")}, None)  # fmt: skip
    assert (bb.Stack(p2).src / "f").read_text() == "2"
    (src / "f").write_text("local edit")
    with pytest.raises(bb.BlackboxError, match="local changes"):
        _ = bb.Stack(p1).src
