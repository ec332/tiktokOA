"""P7 "Done when" on the SYNTHETIC notify fixture (C10), with the real browser: Chromium in
`tp-runner-browser:p7-dev` (C11). The model's proposal is SCRIPTED (K5); running verifies it for
real. A real proposal by the model is I1's.

- A proposal is repaired once from a real locator failure, verified (the create request, then an
  API read-back), and stored; its bound entry passes K = 5 runs plus one shuffled (the gate's
  `decide.stability`).
- The `page-error` seed gives UI_ERROR (product side). A renamed button gives BINDING (test side).

Containers and the network are named `it3-p7-*` and removed afterwards. The network is a plain
bridge so the host can bootstrap the fixture's user; the fixture sends nothing out (no NOTIFY_URL,
no SMTP_HOST)."""

import contextlib
import json
import shutil
import subprocess
import tempfile
import time
import types
from pathlib import Path

import httpx
import pytest

from tp.stages.discover import ui_flows
from tp.stages.discover.discovery import Probes
from tp.stages.gate import decide
from tp.stages.generate import image
from tp.stages.generate import ui as gen_ui
from tp.stages.triage import rules

pytestmark = pytest.mark.docker

ROOT = Path(__file__).parents[1]
NOTIFY = ROOT / "tests" / "fixtures" / "sut" / "notify"
BLACKBOX, BROWSER = "tp-runner-blackbox:p7-dev", "tp-runner-browser:p7-dev"
NET, PW = "it3-p7-net", "p7-fixture-password"
K = decide.DEFAULTS["k"]


def sh(*cmd, **kw) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)
    assert r.returncode == 0, f"{cmd[:3]}: {(r.stderr or r.stdout)[-2000:]}"
    return r.stdout.strip()


@pytest.fixture(scope="module")
def runner():
    """The black-box runner from this checkout's platform_rt, then the browser runner on it."""
    with tempfile.TemporaryDirectory() as ctx:
        sh("uv", "build", "--wheel", "--out-dir", ctx, str(ROOT / "platform_rt"))
        sh("docker", "build", "-q", "-f", str(ROOT / "images/runner-blackbox/Dockerfile"), "-t",
           BLACKBOX, ctx, timeout=1800)  # fmt: skip
    sh("docker", "build", "-q", "-f", str(gen_ui.DOCKERFILE), "--build-arg", f"BASE={BLACKBOX}",
       "-t", BROWSER, str(gen_ui.DOCKERFILE.parent), timeout=3600)  # fmt: skip
    return BROWSER


@pytest.fixture(scope="module")
def net():
    subprocess.run(["docker", "network", "rm", NET], capture_output=True, check=False)
    sh("docker", "network", "create", NET)
    yield NET
    names = sh("docker", "ps", "-aq", "--filter", "name=it3-p7-").split()
    if names:
        subprocess.run(["docker", "rm", "-f", *names], capture_output=True, check=False)
    subprocess.run(["docker", "network", "rm", NET], capture_output=True, check=False)


class App:
    """The notify fixture, from a tmp copy (a seed patched in, or a button renamed), alone on the
    test network as `app`."""

    def __init__(self, tmp: Path, variant: str):
        src = tmp / variant
        shutil.copytree(NOTIFY, src)
        if variant == "page-error":
            sh("patch", "-s", "-p1", "-d", str(src), "-i", str(src / "seeds/page-error.patch"))
        elif variant == "renamed":
            app = src / "app.py"
            app.write_text(app.read_text().replace(">Save note<", ">Store note<"))
        tag = f"it3-p7-notify-{variant}:p7-dev"
        sh("docker", "build", "-q", "-t", tag, str(src))
        for old in sh("docker", "ps", "-aq", "--filter", "name=it3-p7-app").split():
            subprocess.run(["docker", "rm", "-f", old], capture_output=True, check=False)
        self.name = f"it3-p7-app-{variant}"
        sh("docker", "run", "-d", "--name", self.name, "--network", NET, "--network-alias", "app",
           "-p", "127.0.0.1::8080", tag)  # fmt: skip
        port = sh("docker", "port", self.name, "8080").splitlines()[0].rsplit(":", 1)[1]
        self.url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(self.url + "/openapi.json", timeout=1).status_code == 200:
                    break
            time.sleep(0.1)
        r = httpx.post(self.url + "/api/users", json={"username": "tpadmin", "password": PW})
        self.token = r.json()["token"]

    def http(self, method, path, headers=None):
        r = httpx.request(method, self.url + path, headers=headers, timeout=10)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text[:2000]

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, check=False)


def pytest_in_browser(app: App, entry: dict, out: Path, shuffle: str | None = None) -> list[dict]:
    """The bound entry in the browser runner, as `Stack.pytest` runs a test dir (request R2)."""
    tests = out.parent / "tests"
    tests.mkdir(exist_ok=True)
    (tests / entry["file"]).write_text(entry["source"])
    out.mkdir(parents=True)
    env = {"TP_MODE": "blackbox", "TP_BASE_URL": "http://app:8080", "TP_AUTH_SCHEME": "Bearer",
           "TP_RESULTS": "/work/out/results.jsonl", "TP_LEDGER": "/work/out/ledger.jsonl",
           "TP_SECRET_TOKEN": app.token, "TP_SECRET_ADMIN_PASSWORD": PW, "TP_RUN": out.name,
           **({"TP_SHUFFLE": shuffle} if shuffle else {})}  # fmt: skip
    cmd = ["docker", "run", "--rm", "--network", NET, "-v", f"{tests}:/work/tests:ro",
           "-v", f"{out}:/work/out", *[a for k, v in env.items() for a in ("-e", f"{k}={v}")],
           BROWSER, "python", "-m", "pytest", "-p", "platform_rt.blackbox", "-p",
           "no:cacheprovider", "-q", "--continue-on-collection-errors", "--rootdir=/work/tests",
           "/work/tests"]  # fmt: skip
    subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    return [json.loads(ln) for ln in (out / "results.jsonl").read_text().splitlines()]


class Llm:
    """SCRIPTED (K5): a first proposal with a wrong button name, then the right one."""

    def __init__(self):
        self.prompts = []

    def call(self, prompt, **kw):
        self.prompts.append(prompt)
        save = "Save" if len(self.prompts) == 1 else "Save note"
        return json.dumps({"steps": [
            {"action": "goto", "path": "/"},
            {"action": "fill", "role": "textbox", "name": "Username", "value": "tpadmin"},
            {"action": "fill", "role": "textbox", "name": "Password",
             "value": "$TP_SECRET_ADMIN_PASSWORD"},
            {"action": "click", "role": "button", "name": "Sign in"},
            {"action": "fill", "role": "textbox", "name": "Note", "value": "$TP_UNIQUE"},
            {"action": "click", "role": "button", "name": save}]})  # fmt: skip


def discover(app: App, monkeypatch) -> types.SimpleNamespace:
    monkeypatch.setattr(gen_ui, "build_image", lambda: {"digest": BROWSER})
    monkeypatch.setattr(ui_flows.secrets, "get", lambda slug, name: PW)
    d = types.SimpleNamespace(llm=Llm(), probes=Probes(), slug="notify", prov={}, emitted=[],
                              spec=app.http("GET", "/openapi.json")[1],
                              cfg={"model": "claude-haiku-5-5", "admin_user": "tpadmin"},
                              values={"reach": {"strategy": "dockerfile"},
                                      "auth": {"login": {"path": "/api/login"}}},
                              stack=types.SimpleNamespace(egress=lambda image, env:
                                  contextlib.nullcontext((NET, "http://app:8080"))))  # fmt: skip

    def http(method, path, headers=None):
        n, out = d.probes.call("http", f"{method} {path}", lambda: dict(
            zip(("status", "body"), app.http(method, path, headers), strict=True)))  # fmt: skip
        return n, out["status"], out["body"]

    d.http = http
    d.auth_headers = lambda: {"Authorization": f"Bearer {app.token}"}
    d.emit = lambda ev, **kw: d.emitted.append(kw)
    d.mark = lambda c, k, s, layer, n: d.prov.setdefault(c, {}).__setitem__(
        k, {"source": s, "layer": layer, "probe": n})  # fmt: skip
    ui_flows.run(d)
    return d


@pytest.fixture(scope="module")
def stored(runner, net, tmp_path_factory):
    """Discovery on the clean fixture: the flow it verified and stored."""
    app = App(tmp_path_factory.mktemp("clean"), "clean")
    with pytest.MonkeyPatch.context() as mp:
        d = discover(app, mp)
    yield d, app
    app.stop()


def test_a_proposed_flow_is_verified_and_stored(stored):
    d, _ = stored
    assert d.emitted[-1]["flow"] == "create_notes", d.emitted
    (flow,) = d.ui_flows
    assert flow["verified"] == {"op": "POST /api/notes", "read_back": "GET /api/notes"}
    assert d.prov["checks"]["ui create_notes"]["source"] == "ai_proposed+verified"
    assert len(d.llm.prompts) == 2  # the first proposal's button failed, for real:
    assert "LocatorError" in d.llm.prompts[1] and 'button "Save note"' in d.llm.prompts[1]
    snapshot = next(e for e in d.probes.log if e["key"] == "ui snapshot")["out"]["snapshot"]
    assert 'button "Sign in"' in snapshot  # Playwright's aria snapshot of the real page


def test_its_bound_entry_runs_k_of_k_in_the_browser_runner(stored, tmp_path):
    d, app = stored
    entry = gen_ui.entry(d.ui_flows[0])
    runs = [pytest_in_browser(app, entry, tmp_path / f"k{i}") for i in range(K)]
    runs.append(pytest_in_browser(app, entry, tmp_path / "shuffled", shuffle="p7"))
    node = "create_notes.ui.yaml::create_notes[U:create_notes]"
    stab = decide.stability(runs)
    assert stab[node] | {"outcomes": None} == {"outcome": "passed", "origin": None, "stable": True,
                                               "outcomes": None, "case_id": "U:create_notes"}, \
        [r[0].get("failure") for r in runs]  # fmt: skip
    rec = runs[0][0]
    assert rec["kind"] == "ui" and [a["ok"] for a in rec["assertions"]] == [True] * 3
    assert PW not in json.dumps(runs) and rec["observed"] == {}  # evidence on failure only
    texts = {json.dumps(next(e["json"] for e in r[0]["exchanges"] if e["path"] == "/api/notes"
                             and e["method"] == "POST")) for r in runs}  # fmt: skip
    assert len(texts) == K + 1  # $TP_UNIQUE: a new value in each execution
    # the namespace clean-up (blackbox D1) deleted each test's note
    _, notes = app.http("GET", "/api/notes", {"Authorization": f"Bearer {app.token}"})
    assert not [n for n in notes["items"] if n["text"].startswith(("tp-k", "tp-shuffled"))]


@pytest.mark.parametrize(("variant", "origin", "side"), [
    ("page-error", "UI_ERROR", "product"),
    ("renamed", "BINDING", "test"),
])  # fmt: skip
def test_the_seed_is_the_products_fault_and_a_renamed_button_the_tests(stored, tmp_path,
                                                                       variant, origin, side):  # fmt: skip
    d, _ = stored
    app = App(tmp_path, variant)
    try:
        (rec,) = pytest_in_browser(app, gen_ui.entry(d.ui_flows[0]), tmp_path / "run")
    finally:
        app.stop()
    assert (rec["outcome"], rec["origin"]) == ("failed", origin), rec["failure"]
    assert (origin in rules.PRODUCT_SIDE) == (side == "product")
    assert rec["observed"]["screenshot"].endswith(".png")
    assert (tmp_path / "run" / rec["observed"]["screenshot"]).exists()  # on failure only


def test_build_image_publishes_the_browser_runner(runner):
    pub = gen_ui.build_image(tag=BROWSER, base=BLACKBOX)  # cached: same Dockerfile, same base
    assert image.published_browser() == pub and pub["image"] == BROWSER
    versions = sh("docker", "run", "--rm", BROWSER, "python", "-c",
                  "import importlib.metadata as m; print(m.version('playwright'))")  # fmt: skip
    assert versions == "1.64.1"
