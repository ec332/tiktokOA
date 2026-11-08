"""P2 reach and sandbox (docs/status/reach/001): H1, H2 and L5 (DR-9), Dockerfile reach,
compose values with none (D8) and stand-ins (C8). Fixtures only; the notify fixture and the
compose fixtures are SYNTHETIC. Docker tests are marked; their projects and images carry
`it3_p2` (C11)."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from tp.core import profile, secrets
from tp.stages.discover import discovery
from tp.stages.discover import reach as reach_mod
from tp.stages.env import blackbox as bb

FIX = Path(__file__).parent / "fixtures"
NOTIFY, HOSTILE, REQUIRED = (
    FIX / "sut" / "notify",
    FIX / "compose" / "hostile",
    FIX / "compose" / "required",
)
BUILD = {"dockerfile": "Dockerfile", "context": ".", "port": 8080}
STRATS = [("spec", "served"), ("auth", "basic"), ("data", "spec_derived"), ("state", "namespace"),
          ("mutation", "none"), ("safety", "unrestricted"), ("checks", "pytest_cases")]  # fmt: skip


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    bb._STACKS.clear()
    return tmp_path


def repo(src: Path, dest: Path) -> Path:
    """A local git repo of a fixture folder, tagged v1 (the cache key needs a commit)."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("seeds"))
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], ["tag", "v1"]):  # fmt: skip
        subprocess.run(["git", *cmd], cwd=dest, check=True)
    return dest


def prof(path: Path, reach: dict, pdir: Path | None = None) -> profile.TargetProfile:
    raw = {"target": {"name": path.name, "repo": str(path)}, "commit_tested": "v1",
           "reach": {"source": "override", "value": {"strategy": "container", "ref": "v1"} | reach},
           **{c: {"source": "override", "value": {"strategy": s}} for c, s in STRATS}}  # fmt: skip
    return profile.parse(raw, path.name, pdir)


def notify(data: Path, name: str = "it3-p2-notify", **reach) -> profile.TargetProfile:
    """notify through Dockerfile reach, with its seeds as the profile's patches (D6)."""
    pdir = data / "target"
    shutil.copytree(NOTIFY / "seeds", pdir / "patches")
    return prof(
        repo(NOTIFY, data / name), {"build": BUILD, "service": "app", "port": 8080} | reach, pdir
    )


def compose_target(data: Path, src: Path, name: str, edit=None) -> profile.TargetProfile:
    r = repo(src, data / name)
    if edit:
        edit(r)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "e"],
                       cwd=r, check=True)  # fmt: skip
        subprocess.run(["git", "tag", "-f", "v1"], cwd=r, check=True, capture_output=True)
    return prof(r, {"compose": "compose.yml", "service": "app", "port": 8000})


# ---- H1, H2: the hostile fixture ----------------------------------------------------------------


def test_compose_sees_a_minimal_environment(data, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-looking")
    stack = bb.Stack(compose_target(data, HOSTILE, "it3-p2-hostile"))
    stack.write()
    env = stack.compose_env()
    assert "HOME" not in env and env["DOCKER_CONFIG"] and env["PATH"]
    assert "sk-ant-real-looking" not in json.dumps(env)
    # the key-shaped name gets a generated value of its own (D8), never the platform's
    assert env["ANTHROPIC_API_KEY"] == secrets.load(stack.p.slug)["ANTHROPIC_API_KEY"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [(lambda a: a.update(volumes=["${HOME}:/host-home"]), "volume source is refused"),
     (lambda a: a.update(volumes=[{"type": "bind", "source": "$HOME", "target": "/h"}]), "volume source"),
     (lambda a: a.update(env_file=["../../platform.env"]), "outside the repo"),
     (lambda a: a.update(build={"context": "${HOME}"}), "`\\$` in a path"),
     (lambda a: a.update(build={"context": ".", "dockerfile": "../../x"}), "outside the repo")],
)  # fmt: skip
def test_hostile_paths_are_refused(data, mutate, match):
    root = data / "data" / "sut" / "evil"
    root.mkdir(parents=True)
    (data / "data" / "platform.env").write_text("FAKE_API_KEY=sk-not-real\n")
    raw = yaml.safe_load((HOSTILE / "compose.yml").read_text())
    mutate(raw["services"]["app"])
    with pytest.raises(bb.BlackboxError, match=match):
        bb.rewrite(
            raw, root, data / "run", {"service": "app", "port": 8000}, {"app": "x:1"}, repo=root
        )


def test_an_env_file_symlinked_out_of_the_checkout_is_refused(data):
    root = data / "evil"
    root.mkdir()
    (data / "platform.env").write_text("FAKE_API_KEY=sk-not-real\n")
    (root / ".env").symlink_to(data / "platform.env")
    raw = {"services": {"app": {"image": "x:1", "env_file": [".env"]}}}
    with pytest.raises(bb.BlackboxError, match="outside the repo"):
        bb.rewrite(raw, root, data / "run", {"service": "app", "port": 80}, {}, repo=root)


@pytest.mark.docker
def test_hostile_values_resolve_empty_in_compose_itself(data, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-looking")
    stack = bb.Stack(compose_target(data, HOSTILE, "it3-p2-hostile"))
    out = yaml.safe_load(stack.compose("config").stdout)["services"]["app"]
    assert out["environment"]["WHERE"] == "" and "sk-ant" not in json.dumps(out)
    assert out["build"]["args"]["K"] != "sk-ant-real-looking"


# ---- D8: compose values with none --------------------------------------------------------------


def test_compose_vars_kinds():
    text = "a: ${A:?x}\nb: ${B}\nc: ${C:-d}\nd: $D\ne: $${E}\nf: ${A}\ng: ${G+x}\n"
    assert bb.compose_vars(text) == {"A": "required", "B": "bare", "C": "default", "D": "bare",
                                     "G": "default"}  # fmt: skip


def test_required_values_come_from_the_example_and_the_secret_store(data):
    stack = bb.Stack(p := compose_target(data, REQUIRED, "it3-p2-required"))
    stack.write()
    store = secrets.load(p.slug)
    assert stack.values == {"JWT_SECRET": store["JWT_SECRET"], "GREETING": "hello"}
    assert store["JWT_SECRET"] != "changeme"  # a placeholder is never a secret
    assert store["JWT_SECRET"] not in stack.file.read_text()  # process env only, no file
    src = stack.src
    assert not (src / ".env").exists() and not subprocess.run(
        ["git", "status", "--porcelain"], cwd=src, check=False, capture_output=True, text=True).stdout  # fmt: skip


def test_a_required_value_with_none_is_a_setup_question_until_answered(data):
    def drop(r):
        (r / ".env.example").write_text("JWT_SECRET=changeme\n")

    p = compose_target(data, REQUIRED, "it3-p2-required", drop)
    with pytest.raises(bb.MissingValues) as e:
        bb.Stack(p).write()
    assert e.value.names == ["GREETING"]
    reach_mod.store_answers(p.slug, {"var:GREETING": "hi there", "spec": "/x"})
    stack = bb.Stack(p)
    stack.write()
    assert stack.values["GREETING"] == "hi there" and "spec" not in secrets.load(p.slug)


def test_a_missing_env_file_falls_back_to_the_example_or_asks(data):
    root = data / "r"
    root.mkdir()
    raw = {"services": {"app": {"image": "x:1", "env_file": ".env"}}}
    reach = {"service": "app", "port": 80}
    with pytest.raises(bb.MissingValues, match="env_file .env"):
        bb.rewrite(raw, root, data / "run", reach, {}, repo=root)
    (root / ".env.sample").write_text("A=1\n")
    assert bb.rewrite(raw, root, data / "run", reach, {}, repo=root)[0]["services"]["app"][
        "environment"] == {"A": "1"}  # fmt: skip


# ---- L5 (DR-9) and Dockerfile reach ------------------------------------------------------------


def test_dockerfile_reach_reads_the_expose_port(tmp_path):
    assert reach_mod.dockerfile(NOTIFY) == BUILD
    assert reach_mod.dockerfile(tmp_path) is None
    (tmp_path / "Dockerfile").write_text("FROM x\n")
    assert reach_mod.dockerfile(tmp_path)["port"] is None


def test_a_variant_builds_a_patched_copy_and_never_touches_the_clone(data):
    ref = bb.Stack(p := notify(data))
    seeded = ref.variant("bug:webhook-drops-body")
    assert ref.variant("bug:webhook-drops-body") is seeded and ref.variant("reference") is ref
    assert seeded.project == "tp_bb_it3_p2_notify__bug_webhook_drops_body" != ref.project
    a, b = ref.write()["services"]["app"], seeded.write()["services"]["app"]
    assert a["image"].endswith(":" + ref.commit()[:12])
    assert b["image"].startswith(a["image"] + "-") and b["build"]["context"] == str(
        seeded.dir / "src"
    )
    assert "SEEDED DEFECT" in (seeded.dir / "src" / "app.py").read_text()
    assert "SEEDED DEFECT" not in (ref.src / "app.py").read_text()
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=ref.src, check=False, capture_output=True,
                              text=True).stdout  # fmt: skip
    assert seeded.token_secret == "token@bug:webhook-drops-body" and ref.token_secret == "token"
    assert bb.Stack(p, "bug:webhook-drops-body+page-error").patch_hashes().keys() == {
        "page-error", "webhook-drops-body"}  # fmt: skip


@pytest.mark.parametrize(
    ("variant", "match"),
    [("bug:no-such-seed", "no patches/no-such-seed.patch"), ("mutant:x", "bad variant")],
)  # fmt: skip
def test_unknown_variants_are_refused(data, variant, match):
    with pytest.raises(bb.BlackboxError, match=match):
        bb.Stack(notify(data), variant)


def test_an_image_only_target_refuses_a_variant(data):
    r = data / "it3-p2-image"
    r.mkdir()
    (r / "compose.yml").write_text("services:\n  app:\n    image: x:1\n    ports: ['80:80']\n")
    repo_path = repo(r, data / "it3-p2-image-repo")
    pdir = data / "t"
    (pdir / "patches").mkdir(parents=True)
    (pdir / "patches" / "s.patch").write_text("")
    p = prof(repo_path, {"compose": "compose.yml", "service": "app", "port": 80}, pdir)
    with pytest.raises(bb.BlackboxError, match="image-only"):
        bb.Stack(p, "bug:s").write()
    with pytest.raises(bb.BlackboxError, match="image-only"):
        bb.Reach(bb.Stack(p)).provision("bug:s")


# ---- stand-ins (C8) -----------------------------------------------------------------------------

HOOK = {"name": "hook", "kind": "http", "image": "tp-it3-p2-standin:p2-dev", "port": 8000,
        "journal": "/", "env": {"NOTIFY_URL": "http://tp-standin-hook:8000/"}}  # fmt: skip


def test_standins_join_the_run_network_before_the_app(data):
    out, _ = bb.rewrite({"services": {"app": {"image": "x:1", "depends_on": ["db"]}, "db": {"image": "d"}}},
                        data, data / "run", {"service": "app", "port": 80, "standins": [HOOK]}, {})  # fmt: skip
    hook, app = out["services"]["tp-standin-hook"], out["services"]["app"]
    assert hook["image"] == HOOK["image"] and hook["networks"] == ["run"]
    assert app["depends_on"] == ["db", "tp-standin-hook"]
    assert app["environment"]["NOTIFY_URL"] == "http://tp-standin-hook:8000/"
    p = prof(data, {"standins": [HOOK]})
    assert bb.families_config(p)["standins"] == [
        {"name": "hook", "kind": "http", "journal": "/", "url": "http://tp-standin-hook:8000"}
    ]
    assert "standins" not in bb.families_config(prof(data, {}))


# ---- Docker: the done-when runs -----------------------------------------------------------------


class NoModel:
    """Scripted model (K5): no admin env documented, no files picked."""

    def call(self, *a, **kw):
        return '{"files": [], "candidates": []}'


def _rm_images(*prefixes: str) -> None:
    out = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                         check=False, capture_output=True, text=True).stdout.split()  # fmt: skip
    gone = [i for i in out if i.startswith(prefixes)]
    if gone:
        subprocess.run(["docker", "image", "rm", "-f", *gone], check=False, capture_output=True)


def _in(container: str, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", container, "python", "-c", code], check=False, capture_output=True,
                          text=True, timeout=60)  # fmt: skip


@pytest.mark.docker
def test_notify_onboards_by_dockerfile_reach_and_its_seed_builds_and_serves(data):
    p = notify(data)  # a local path; its seeds are the profile's patches
    d = discovery.Discovery(p.repo, "v1", NoModel(), discovery.Probes())
    seeded = bb.Stack(p, "bug:webhook-drops-body")
    try:
        d.step_reach()
        assert d.values["reach"]["build"] == BUILD and "compose" not in d.values["reach"]
        assert d.stack.http("GET", "/openapi.json")[0] == 200  # the clean build serves
        seeded.start(wait=False)
        seeded.wait_http("/openapi.json", timeout=120)
        image = seeded.write()["services"]["app"]["image"]
        got = subprocess.run(["docker", "run", "--rm", image, "grep", "-c", "SEEDED", "app.py"],
                             check=False, capture_output=True, text=True)  # fmt: skip
        assert got.stdout.strip() == "1"  # the seeded build, not the reference
    finally:
        d.stack.down()
        seeded.down()
        _rm_images("tp-it3_p2_notify-")


@pytest.mark.docker
def test_required_values_onboard_and_stay_out_of_the_checkout(data):
    stack = bb.Stack(p := compose_target(data, REQUIRED, "it3-p2-required"))
    try:
        stack.start(wait=False)
        stack.wait_http("/", timeout=120)
        env = _in(f"{stack.project}-app-1", "import os, json; print(json.dumps(dict(os.environ)))")
        env = json.loads(env.stdout)
        assert env["JWT_SECRET"] == secrets.load(p.slug)["JWT_SECRET"]
        assert (env["GREETING"], env["MODE"], env["OPTIONAL"]) == ("hello", "prod", "")
        assert not subprocess.run(["git", "status", "--porcelain"], cwd=stack.src,
                                  check=False, capture_output=True, text=True).stdout  # fmt: skip
    finally:
        stack.down()
        _rm_images("tp-it3_p2_required-")


@pytest.mark.docker
def test_a_standin_is_reachable_from_app_and_runner_and_nothing_leaves(data):
    stub = data / "stub"
    stub.mkdir()
    (stub / "Dockerfile").write_text(
        'FROM python:3.14-slim\nCMD ["python", "-m", "http.server", "8000"]\n'
    )
    subprocess.run(["docker", "build", "-q", "-t", HOOK["image"], str(stub)], check=True,
                   capture_output=True)  # fmt: skip
    stack = bb.Stack(notify(data, standins=[HOOK]))
    try:
        stack.start(wait=False)
        stack.wait_http("/openapi.json", timeout=120)
        app = f"{stack.project}-app-1"
        hit = "import os, urllib.request as u; print(u.urlopen(os.environ['NOTIFY_URL'], timeout=5).status)"
        assert _in(app, hit).stdout.strip() == "200"
        runner = subprocess.run(["docker", "run", "--rm", "--network", f"{stack.project}_run",
                                 HOOK["image"], "python", "-c",
                                 "import urllib.request as u; print(u.urlopen('http://tp-standin-hook:8000/', timeout=5).status)"],
                                check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        assert runner.stdout.strip() == "200"
        # TEST-NET-3 (RFC 5737): never routed, so nothing real is contacted even on a leak
        out = _in(app, "import urllib.request as u; u.urlopen('http://203.0.113.1/', timeout=3)")
        assert out.returncode != 0
        assert httpx.get(stack.base_url + "/openapi.json").status_code == 200  # inbound still works
    finally:
        stack.down()
        _rm_images("tp-it3_p2_notify-", HOOK["image"])
