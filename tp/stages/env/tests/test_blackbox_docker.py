"""The Docker halves of the black-box contract tests (blackbox D12): Paperless for real, and a
3-line compose fixture for the build cache, the internal network and the forwarder."""

import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from tp.core import profile, secrets, strategies
from tp.stages.env import blackbox as bb
from tp.stages.generate import image

pytestmark = pytest.mark.docker
FIXTURE = Path(__file__).parents[4] / "tests" / "fixtures" / "targets" / "paperless-ngx"
TEST = """
import pytest
from platform_rt import api, eventually


@pytest.mark.tp_case(unit="TC-bb")
def test_tag_round_trip(make):
    token, name = make.admin()["token"], make.name("tag")
    assert api.post("/api/tags/", token=token, json={"name": name}).status_code == 201
    got = eventually(lambda: api.get("/api/tags/", token=token, params={"name__iexact": name}),
                     lambda r: r.json()["count"] == 1)
    assert got.json()["results"][0]["name"] == name
    assert api.get("/api/tags/").status_code == 401
"""


def _offline(container: str) -> bool:
    probe = "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=5)"
    cmd = ["docker", "exec", container, "python3", "-c", probe]
    return subprocess.run(cmd, check=False, capture_output=True).returncode != 0


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    bb._STACKS.clear()
    return tmp_path


def test_paperless_end_to_end_through_the_black_box_runner(data, monkeypatch):
    monkeypatch.setenv("TP_TARGET", str(FIXTURE))
    image.build_blackbox()
    s = strategies.resolve(profile.load())
    try:
        spec = s.reach.provision()
        assert {p["name"]: p["ok"] for p in spec["probes"]} == {"health": True, "auth": True}
        assert len(s.spec.openapi()["paths"]) > 50
        (data / "t").mkdir()
        (data / "t" / "test_tags.py").write_text(TEST)
        r = s.checks.run(data / "t", data / "out")
        assert [x["outcome"] for x in r["results"]] == ["passed"], r["output"]
        assert r["cleanup"] == {
            "deleted": 0,
            "failed": 0,
            "unknown_delete_route": 0,
            "deleted_by_test": 1,
        }  # the test's own teardown (blackbox/006)
        stack = s.reach.stack
        basic = replace(stack.p, fields=stack.p.fields | {"auth": profile.Field(
            {"strategy": "basic", "user": "tpadmin", "password": {"secret": "admin_password"}},
            "override")})  # fmt: skip
        who = bb.BasicAuth(basic).principal("admin")  # the same admin, over basic auth (D7)
        assert stack.http("GET", "/api/tags/", headers=bb.header(who))[0] == 200
        wrong = bb.header({"scheme": "Basic", "token": "dHBhZG1pbjpub3Bl"})  # tpadmin:nope
        assert stack.http("GET", "/api/tags/", headers=wrong)[0] in (401, 403)
        app = f"{s.reach.stack.project}-webserver-1"
        assert _offline(app)  # no internet at run time (D5)
        assert secrets.leaks(data) == []  # the redaction test (D9)
    finally:
        s.reach.teardown()


def test_build_once_per_commit_on_the_internal_network(data, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text(
        'FROM python:3.14-slim\nCMD ["python", "-m", "http.server", "8000"]\n'
    )
    (repo / "compose.yml").write_text("services:\n  app:\n    build: .\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                "commit", "-qm", "x"], ["tag", "v1"]):  # fmt: skip
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    p = profile.parse({
        "target": {"name": "tiny", "repo": str(repo)}, "commit_tested": "v1",
        "reach": {"source": "override", "value": {"strategy": "container", "ref": "v1",
                  "compose": "compose.yml", "service": "app", "port": 8000}},
        **{c: {"source": "override", "value": {"strategy": s}} for c, s in [
            ("spec", "served"), ("auth", "basic"), ("data", "spec_derived"),
            ("state", "namespace"), ("mutation", "none"), ("safety", "unrestricted"),
            ("checks", "pytest_cases")]},
    }, "tiny-http")  # fmt: skip
    stack = bb.Stack(p)
    try:
        assert stack.build() == ["app"]
        assert stack.build() == []  # cached for this commit (D4)
        stack.compose("up", "-d", "--wait", timeout=300)
        stack.wait_http("/", timeout=60)
        assert httpx.get(stack.base_url + "/").status_code == 200  # through the forwarder
        app = f"{stack.project}-app-1"
        assert _offline(app)
    finally:
        stack.down()
        subprocess.run(["docker", "image", "rm", "-f", f"tp-tiny_http-app:{stack.commit()[:12]}"],
                       check=False, capture_output=True)  # fmt: skip
