"""P6 (docs/status/standins/001): the stand-ins, the dependency families and discovery's wiring,
on the SYNTHETIC notify fixture (C10) and its seeds. The known answers (DR-8): on the clean
build both families pass; `webhook-drops-body` and `invite-wrong-recipient` fail
`dependency_called`; `webhook-blocks` fails `dependency_failure_handled`.

The app and the HTTP fake run as local processes. Mailpit (SMTP) needs Docker: those tests are
marked `docker`, and their container is named `it3-p6-*`."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
from platform_rt.expect import ExpectationFailed

from platform_rt import api, dependency, families, record, standin
from tp.stages.discover import dependencies as deps
from tp.stages.discover.discovery import Probes

NOTIFY = Path(__file__).parent / "fixtures" / "sut" / "notify"
MAILPIT = deps.config()["standins"]["smtp"]["image"]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 15) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise TimeoutError(url)


@pytest.fixture
def fake():
    server = standin.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    standin.JOURNAL.clear()
    standin.FAULT.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class App:
    """The notify fixture, with an optional seed applied to a tmp copy, as a local process."""

    def __init__(self, tmp: Path, seed: str | None = None):
        self.dir = tmp / "app"
        self.dir.mkdir(parents=True)
        shutil.copy(NOTIFY / "app.py", self.dir / "app.py")
        if seed:
            subprocess.run(["patch", "-s", "-p1", "-d", str(self.dir), "-i",
                            str(NOTIFY / "seeds" / f"{seed}.patch")], check=True)  # fmt: skip
        self.proc = None

    def start(self, env: dict) -> str:
        self.stop()
        port = _port()
        self.proc = subprocess.Popen([sys.executable, "app.py"], cwd=self.dir,
                                     env={"PATH": os.environ["PATH"], "PORT": str(port)} | env,
                                     stderr=subprocess.DEVNULL)  # fmt: skip
        self.url = f"http://127.0.0.1:{port}"
        _wait(self.url + "/openapi.json")
        return self.url

    def sign_up(self) -> str:
        r = httpx.post(self.url + "/api/users", json={"username": "tp", "password": "pw"})
        return r.json()["token"]

    def stop(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def app(tmp_path):
    apps = []

    def make(seed=None):
        apps.append(App(tmp_path / f"a{len(apps)}", seed))
        return apps[-1]

    yield make
    for a in apps:
        a.stop()


@pytest.fixture
def target(monkeypatch):
    """Point platform_rt at a running app: -> run(case_id, c) -> the record, raising on a fail."""

    def bind(url: str, token: str):
        monkeypatch.setenv("TP_BASE_URL", url)
        monkeypatch.setenv("TP_SECRET_TOKEN", token)
        api.use(None)
        spec = httpx.get(url + "/openapi.json").json()

        def run(case_id: str, c: dict):
            rec = record.start(case_id, case_id)
            try:
                families.run(case_id, spec, families.DEFAULTS | c)
                rec.observed["outcome"] = "passed"
            except ExpectationFailed:
                rec.observed["outcome"] = "failed"
            return rec

        return run

    yield bind
    api.use(None)


WEBHOOK = {"name": "http", "kind": "http", "via": "env", "key": "NOTIFY_URL",
           "trigger": "POST /api/notes", "status": "wired"}  # fmt: skip
FAST = {"dependency_delay_s": 3, "dependency_deadline_s": 1.5}


def _http_conf(fake: str) -> dict:
    return FAST | {"standins": [{"name": "http", "kind": "http", "url": fake,
                                 "journal": "/__journal"}], "dependencies": [WEBHOOK]}  # fmt: skip


@pytest.mark.parametrize(("seed", "called", "handled"), [
    (None, "passed", "passed"),
    ("webhook-drops-body", "failed", "passed"),
    ("webhook-blocks", "passed", "failed"),
])  # fmt: skip
def test_webhook_known_answers(fake, app, target, seed, called, handled):
    a = app(seed)
    a.start({"NOTIFY_URL": fake + "/notify_url"})
    run, c = target(a.url, a.sign_up()), _http_conf(fake)
    rec = run("G:dependency_called:POST /api/notes", c)
    assert rec.observed["outcome"] == called
    assert rec.fault is None
    rec = run("G:dependency_failure_handled:POST /api/notes", c)
    assert rec.observed["outcome"] == handled
    assert rec.fault == {"standin": "http", "mode": "delay", "value": 3.0}  # C1
    assert httpx.get(fake + "/__journal").status_code == 200 and not standin.FAULT  # cleared


def test_fake_journals_masked_and_faults(fake):
    httpx.post(fake + "/hook", json={"a": 1}, headers={"Authorization": "Bearer s3cret",
                                                       "X-Api-Key": "k"})  # fmt: skip
    httpx.post(fake + "/__fault", json={"status": 503, "delay_s": 0.2})
    t0 = time.monotonic()
    assert httpx.post(fake + "/hook").status_code == 503 and time.monotonic() - t0 >= 0.2
    httpx.post(fake + "/__fault", json={})
    assert httpx.post(fake + "/hook").status_code == 200
    j = httpx.get(fake + "/__journal").json()
    assert [e["path"] for e in j] == ["/hook"] * 3
    assert "s3cret" not in json.dumps(j) and j[0]["headers"]["Authorization"] == "<masked>"
    assert j[0]["headers"]["X-Api-Key"] == "<masked>" and json.loads(j[0]["body"]) == {"a": 1}


def test_plan_reads_wired_triggers_only():
    c = families.DEFAULTS | {"dependencies": [WEBHOOK]}
    spec = {"paths": {"/api/notes": {"post": {}}}}
    ids = [cid for cid, _ in families.plan(spec, c)]
    assert "G:dependency_called:POST /api/notes" in ids
    assert "G:dependency_failure_handled:POST /api/notes" in ids
    assert not [i for i in families.plan(spec, families.DEFAULTS) if "dependency" in i[0]]


def test_ledger():
    rows = [{"case_id": "G:dependency_called:POST /api/notes", "outcome": "failed", "fault": None,
             "observed": {"received": [{"path": "/notify_url", "body": "{}"}]}},
            {"case_id": "G:read_back:POST /api/notes", "outcome": "passed"}]  # fmt: skip
    assert dependency.ledger(rows) == {
        "G:dependency_called:POST /api/notes": {
            "outcome": "failed",
            "fault": None,
            "received": [{"path": "/notify_url", "body": "{}"}],
        }
    }


# ---- discovery ---------------------------------------------------------------------------------


class FakeD:
    """The parts of `Discovery` the hook uses, over a local app; `restart` maps the stand-in
    hosts to local addresses (P2 provisions the real ones on the run network)."""

    def __init__(self, a: App, src: Path, local: dict, llm=None):
        self.a, self.src, self.local, self.llm = a, src, local, llm
        self.app_env, self.errors_5xx, self.prov = {}, [], {}
        self.values = {"reach": {"env": {}}, "auth": {"bootstrap": {"path": "/api/users"}}}
        self.public, self.probes, self.cfg = ["POST /api/login"], Probes(), {"model": "haiku"}
        self.spec = httpx.get(a.start({}) + "/openapi.json").json()
        self.token = a.sign_up()
        self.stack = types.SimpleNamespace(standin_url=lambda name: local[name]["url"])

    def restart(self):
        env = {}
        for s in self.values["reach"]["standins"]:
            for k, v in s["env"].items():
                env[k] = v.replace(f"tp-standin-{s['name']}:{s['port']}", self.local[s["name"]]
                                   .get("host", "127.0.0.1")).replace(f"tp-standin-{s['name']}", "127.0.0.1")  # fmt: skip
                if k == "SMTP_PORT":
                    env[k] = self.local["mail"]["smtp"]
        self.a.start(env)
        self.token = self.a.sign_up()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def http(self, method, path, *, headers=None, json_body=None, **_):
        r = httpx.request(method, self.a.url + path, headers=headers, json=json_body)
        self.probes.log.append({"kind": "http", "key": f"{method} {path}"})
        return len(self.probes.log) - 1, r.status_code, r.json() if r.content else None

    def mark(self, concern, key, source, layer, probe=None):
        self.prov.setdefault(concern, {})[key] = {"source": source, "layer": layer, "probe": probe}

    def emit(self, *a, **k):
        pass


@pytest.fixture
def repo(tmp_path):
    def make(files: dict) -> Path:
        src = tmp_path / "repo"
        src.mkdir()
        for name, text in files.items():
            (src / name).write_text(text)
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)
        subprocess.run(["git", "add", "."], cwd=src, check=True)
        return src

    return make


def test_discovery_wires_notify_url(fake, app, repo, monkeypatch):
    port = fake.rsplit(":", 1)[1]
    d = FakeD(app(), repo({"app.py": (NOTIFY / "app.py").read_text()}),
              {"http": {"url": fake, "host": f"127.0.0.1:{port}"},
               "mail": {"url": "http://127.0.0.1:9", "smtp": "9"}})  # no Mailpit here  # fmt: skip
    monkeypatch.setattr(deps, "restart", lambda d: d.restart())
    deps.run(d)
    by_key = {e["key"]: e for e in d.dependencies}
    assert by_key["NOTIFY_URL"] | {"evidence": None} == {
        "name": "http", "kind": "http", "via": "env", "key": "NOTIFY_URL", "status": "wired",
        "trigger": "POST /api/notes", "setup": None, "evidence": None,
        "reason": "POST /api/notes left a line in tp-standin-http's journal"}  # fmt: skip
    assert (
        by_key["SMTP_HOST"]["status"] == "unwired" and "not tested" in by_key["SMTP_HOST"]["reason"]
    )
    assert [s["name"] for s in d.values["reach"]["standins"]] == ["http"]  # unwired: dropped
    assert d.values["reach"]["standins"][0]["env"] == {
        "NOTIFY_URL": "http://tp-standin-http:8000/notify_url"
    }
    assert d.prov["checks"]["dependency NOTIFY_URL"]["source"] == "probed"


def test_model_proposes_only_without_a_convention(repo, app):
    calls = []
    llm = types.SimpleNamespace(
        call=lambda prompt, **kw: (
            calls.append(prompt)
            or json.dumps(
                {"candidates": [{"env": "APP_HOOK_TARGET", "kind": "http", "quote": "hook_target"}]}
            )
        )
    )
    src = repo({"hooks.py": 'cfg = settings.get("hook_target")  # posts the webhook\n'})
    d = FakeD(app(), src, {}, llm)
    d.spec = {"paths": {}}
    cands, _, _ = deps.candidates(d, deps.config())
    assert [(c["key"], c["layer"]) for c in cands] == [("APP_HOOK_TARGET", "exploration")]
    assert len(calls) == 1 and "hook_target" in calls[0]
    (src / "hooks.py").write_text('URL = os.environ["ORDER_WEBHOOK_URL"]  # webhook\n')
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    cands, _, _ = deps.candidates(d, deps.config())
    assert [c["key"] for c in cands] == ["ORDER_WEBHOOK_URL"] and len(calls) == 1  # no call


def test_spec_registered_webhook_and_guard(fake, repo, app):
    """`via: spec`: a body field named url registers the stand-in, then a create triggers it."""
    d = FakeD(app(), repo({"x.py": 'os.environ.get("ALLOW_INTERNAL_HOSTS")\n'}),
              {"http": {"url": fake}})  # fmt: skip
    d.spec = {"paths": {"/hooks": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"type": "object", "required": ["url"],
                   "properties": {"url": {"type": "string"}}}}}}}}}}  # fmt: skip
    cands, guards, _ = deps.candidates(d, deps.config())
    assert [(c["via"], c["key"], c["op"]) for c in cands] == [("spec", "url", "POST /hooks")]
    assert guards == ["ALLOW_INTERNAL_HOSTS"]
    sts = deps.plan(cands, guards, [], deps.config())
    assert sts[0]["env"] == {"ALLOW_INTERNAL_HOSTS": "tp-standin-http"}
    sent = []

    def http(method, path, *, json_body=None, **_):  # registering, then a ping to the URL
        sent.append((method, path, json_body))
        httpx.post(fake + "/url", json={"ping": 1})
        return len(sent), 201, {}

    d.http, d.values["auth"] = http, {}
    deps.verify(d, cands, sts, deps.config())
    c = cands[0]
    assert c["setup"] == {"op": "POST /hooks", "body": {"url": "http://tp-standin-http:8000/url"}}
    assert c["status"] == "unwired"  # its own setup op is never the trigger, and nothing else was
    assert sent == [("POST", "/hooks", {"url": "http://tp-standin-http:8000/url"})]


# ---- SMTP: Mailpit (Docker) --------------------------------------------------------------------


@pytest.fixture
def mailpit():
    name, web, smtp = f"it3-p6-mailpit-{os.getpid()}", _port(), _port()
    cmd = deps.config()["standins"]["smtp"]["command"]
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{web}:8025",
                    "-p", f"127.0.0.1:{smtp}:25", MAILPIT, *cmd], check=True,
                   capture_output=True)  # fmt: skip
    url = f"http://127.0.0.1:{web}"
    _wait(url + "/api/v1/messages", 60)
    yield {"url": url, "smtp": str(smtp)}
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


INVITE = {"name": "mail", "kind": "smtp", "via": "env", "key": "SMTP_HOST",
          "trigger": "POST /api/invite", "status": "wired"}  # fmt: skip


@pytest.mark.docker
@pytest.mark.parametrize(("seed", "called"), [(None, "passed"),
                                              ("invite-wrong-recipient", "failed")])  # fmt: skip
def test_invite_known_answers(mailpit, app, target, seed, called):
    a = app(seed)
    a.start({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": mailpit["smtp"]})
    run = target(a.url, a.sign_up())
    c = {"standins": [{"name": "mail", "kind": "smtp", "url": mailpit["url"],
                       "journal": "/api/v1/messages"}], "dependencies": [INVITE]}  # fmt: skip
    rec = run("G:dependency_called:POST /api/invite", c)
    assert rec.observed["outcome"] == called
    rec = run("G:dependency_failure_handled:POST /api/invite", c)
    assert rec.observed["outcome"] == "passed"  # the clean invite answers 502 under the refusal
    assert rec.under_test["status"] == 502
    assert rec.fault == {"standin": "mail", "mode": "smtp", "value": 451}
    assert httpx.get(mailpit["url"] + "/api/v1/chaos").json()["Recipient"]["Probability"] == 0


@pytest.mark.docker
def test_discovery_wires_both_on_notify(fake, mailpit, app, repo, monkeypatch):
    port = fake.rsplit(":", 1)[1]
    d = FakeD(app(), repo({"app.py": (NOTIFY / "app.py").read_text()}),
              {"http": {"url": fake, "host": f"127.0.0.1:{port}"}, "mail": mailpit})  # fmt: skip
    monkeypatch.setattr(deps, "restart", lambda d: d.restart())
    deps.run(d)
    got = {e["key"]: (e["status"], e["trigger"]) for e in d.dependencies}
    assert got == {"NOTIFY_URL": ("wired", "POST /api/notes"),
                   "SMTP_HOST": ("wired", "POST /api/invite")}  # fmt: skip
    mail = next(s for s in d.values["reach"]["standins"] if s["name"] == "mail")
    assert mail["env"] == {"SMTP_HOST": "tp-standin-mail", "SMTP_PORT": "25"}
    assert mail["command"] == ["--enable-chaos", "--smtp", "0.0.0.0:25"]
