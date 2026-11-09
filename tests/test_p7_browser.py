"""P7 (docs/status/browser/001): UI flows as test cases in their own format (DR-4), proposed once
at discover time and verified by running (DR-1). These tests use a SCRIPTED fake page, modelled
on the SYNTHETIC notify fixture's page (C10), and scripted model answers (K5): Playwright isn't
on the host. The real browser runs in `tests/test_p7_docker.py`."""

import contextlib
import json
import os
import types
from pathlib import Path

import httpx
import pytest
import yaml
from platform_rt.api import api as client
from platform_rt.expect import ExpectationFailed
from platform_rt.origin import LocatorError, UiPageError

from platform_rt import record, ui
from tp.stages.discover import ui_flows
from tp.stages.discover.discovery import Probes
from tp.stages.generate import image, stage
from tp.stages.generate import ui as gen_ui

pytest_plugins = ["pytester"]

BASE = "http://app:8080"
# The notify page as a fake Playwright page: sign-in reveals the note form, saving POSTs the
# note; the `page-error` seed throws in the page's script on save (as seeds/page-error.patch).
FAKE = """
import json


class TimeoutError(Exception):
    pass


TimeoutError.__module__ = "playwright._impl._errors"  # what platform_rt.ui tells apart


class Req:
    def __init__(self, method, url, body=None, auth=False, rtype="fetch"):
        self.method, self.url, self.post_data, self.resource_type = method, url, body, rtype
        self.headers = {"authorization": "Bearer t"} if auth else {}


class Resp:
    def __init__(self, req, status, body):
        self.request, self.status, self._body = req, status, body

    def body(self):
        return self._body.encode() if isinstance(self._body, str) else json.dumps(self._body).encode()


class App:
    def __init__(self, seed=None, save="Save note", status=201):
        self.seed, self.save, self.status = seed, save, status
        self.notes, self.fields, self.signed_in = [], {}, False


class Page:
    def __init__(self, app):
        self.app, self.handlers, self.shots = app, {}, []

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def emit(self, event, x):
        for fn in self.handlers.get(event, []):
            fn(x)

    def elements(self):
        out = [("textbox", "Username"), ("textbox", "Password"), ("button", "Sign in")]
        return out + [("textbox", "Note"), ("button", self.app.save)] if self.app.signed_in else out

    def goto(self, url, wait_until=None, timeout=None):
        self.emit("response", Resp(Req("GET", url, rtype="document"), 200, "<html></html>"))

    def get_by_role(self, role, name=None, exact=False):
        return Loc(self, role, name)

    def wait_for_timeout(self, ms):
        pass

    def locator(self, sel):
        return Snap(self)

    def screenshot(self, path):
        self.shots.append(path)
        open(path, "wb").write(b"png")


class Snap:
    def __init__(self, page):
        self.page = page

    def aria_snapshot(self):
        return "\\n".join(f'- {r} "{n}"' for r, n in self.page.elements())


class Loc:
    def __init__(self, page, role, name):
        self.page, self.role, self.name = page, role, name

    def _find(self):
        if (self.role, self.name) not in self.page.elements():
            raise TimeoutError(f"Locator.click: Timeout 5000ms exceeded.\\n  waiting for "
                               f"get_by_role({self.role!r}, name={self.name!r})")

    def fill(self, value, timeout=None):
        self._find()
        self.page.app.fields[self.name] = value

    def click(self, timeout=None):
        self._find()
        app, page = self.page.app, self.page
        if self.name == "Sign in":
            body = json.dumps({"username": app.fields.get("Username"),
                               "password": app.fields.get("Password")})
            app.signed_in = True
            page.emit("response", Resp(Req("POST", "http://app:8080/api/login", body), 200,
                                       {"username": "tpadmin", "token": "session-token-123"}))
            return
        note = {"id": len(app.notes) + 1, "text": app.fields.get("Note")}
        if app.status < 300:
            app.notes.append(note)
        page.emit("response", Resp(Req("POST", "http://app:8080/api/notes?x=1",
                                       json.dumps({"text": note["text"]}), auth=True),
                                   app.status, note if app.status < 300 else {"error": "boom"}))
        if app.seed == "page-error":
            page.emit("pageerror", "TypeError: window.notes.render is not a function")
"""
fake = types.ModuleType("p7_fakepage")
exec(FAKE, fake.__dict__)  # noqa: S102 — our own test double, written out for pytester too

ACTIONS = [
    {"goto": "/"},
    {"fill": {"role": "textbox", "name": "Username", "value": "tpadmin"}},
    {"fill": {"role": "textbox", "name": "Password", "value": "$TP_SECRET_ADMIN_PASSWORD"}},
    {"click": {"role": "button", "name": "Sign in"}},
    {"fill": {"role": "textbox", "name": "Note", "value": "$TP_UNIQUE"}},
    {"click": {"role": "button", "name": "Save note"}},
]
FLOW = {"name": "create_notes", "steps": ACTIONS + [
    {"expect_request": {"method": "POST", "path": "/api/notes", "status": "2xx"}},
    {"read_back": {"path": "/api/notes", "contains": "$TP_UNIQUE"}}]}  # fmt: skip


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_BASE_URL", BASE)
    monkeypatch.setenv("TP_SECRET_ADMIN_PASSWORD", "pw-very-secret")
    monkeypatch.setenv("TP_SECRET_TOKEN", "admin-token")
    monkeypatch.setenv("TP_RESULTS", str(tmp_path / "results.jsonl"))
    monkeypatch.setenv("TP_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("TP_READ_BACK_TIMEOUT", "0")
    return tmp_path


def run_flow(app, flow=FLOW):
    """-> (recorder, page) after running `flow` on a fake page; the API reads the app's notes."""
    client.use(httpx.Client(base_url=BASE, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"items": app.notes}))))  # fmt: skip
    rec, page = record.start("t", "U:create_notes"), fake.Page(app)
    try:
        ui.execute(ui.parse(flow), page)
    finally:
        client.use(None)
    return rec, page


# ---- the IR ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("flow", "why"), [
    ({"name": "x", "steps": []}, "one or more"),
    ({"name": "Bad Name", "steps": [{"goto": "/"}]}, "snake_case"),
    ({"name": "x", "steps": [{"click": {"selector": "#save"}}]}, "click takes"),
    ({"name": "x", "steps": [{"click": {"role": "button", "name": "Save", "css": "b"}}]}, "takes"),
    ({"name": "x", "steps": [{"click": {"role": "button[x]", "name": "Save"}}]}, "takes"),
    ({"name": "x", "steps": [{"goto": "http://elsewhere/"}]}, "a path"),
    ({"name": "x", "steps": [{"goto": "/", "click": {}}]}, "one-key map"),
    ({"name": "x", "steps": [{"hover": {"role": "button", "name": "a"}}]}, "one of"),
    ({"name": "x", "steps": [{"fill": {"role": "textbox", "name": "a", "value": True}}]}, "takes"),
    ({"name": "x", "steps": [{"fill": {"role": "textbox", "name": "a",
                                       "value": "$TP_SECRET_TOKEN"}}]}, "TP_SECRET_TOKEN"),
])  # fmt: skip
def test_only_role_and_name_locators_and_known_placeholders_parse(flow, why):
    with pytest.raises(ui.FlowError, match=why.replace("[", r"\[")):
        ui.parse(flow)


def test_the_bound_flow_parses():
    assert ui.parse(yaml.safe_load(gen_ui.entry(FLOW)["source"])) == FLOW


# ---- the interpreter ------------------------------------------------------------------------


def test_a_clean_flow_records_actions_and_responses_without_secrets(env):
    rec, page = run_flow(fake.App())
    ui_steps = [e for e in rec.exchanges if e.get("kind") == "ui"]
    http = [e for e in rec.exchanges if e.get("kind") != "ui"]
    assert [e["path"] for e in ui_steps] == [
        "goto /", 'fill textbox "Username"', 'fill textbox "Password"', 'click button "Sign in"',
        'fill textbox "Note"', 'click button "Save note"']  # fmt: skip
    assert all(e["status"] == 0 and e["method"] == "UI" for e in ui_steps)
    assert [(e["method"], e["path"], e["status"]) for e in http] == [
        ("GET", "/", 200), ("POST", "/api/login", 200), ("POST", "/api/notes", 201),
        ("GET", "/api/notes", 200)]  # fmt: skip
    text = json.dumps(rec.exchanges)
    assert "pw-very-secret" not in text and "session-token-123" not in text  # redacted
    assert ui_steps[2]["json"] == {"value": "$TP_SECRET_ADMIN_PASSWORD"}  # as written
    assert rec.under_test["path"] == "/api/notes" and rec.under_test["query"] == {"x": "1"}
    assert rec.under_test["auth"] is True
    assert [a["ok"] for a in rec.assertions] == [True, True, True]
    typed = rec.name("ui")
    assert rec.under_test["json"] == {"text": typed}  # $TP_UNIQUE: new for each execution
    ledger = [json.loads(ln) for ln in (env / "ledger.jsonl").read_text().splitlines()]
    assert ledger == [{"path": "/api/notes", "id": 1, "location": None}] == rec.created
    assert page.shots == [] and rec.observed == {}  # evidence on failure only


def test_a_page_error_is_the_products_side_with_a_screenshot(env):
    with pytest.raises(UiPageError, match="window.notes.render"):
        run_flow(fake.App(seed="page-error"))
    rec = record.current()
    assert rec.observed["screenshot"] == f"ui/{rec.nonce}.png"
    assert (env / "ui" / f"{rec.nonce}.png").exists()
    assert 'button "Save note"' in rec.observed["snapshot"]


def test_a_renamed_button_is_a_locator_error(env):
    with pytest.raises(LocatorError, match='click button "Save note": Locator.click: Timeout'):
        run_flow(fake.App(save="Store note"))
    assert 'button "Store note"' in record.current().observed["snapshot"]


def test_a_5xx_create_fails_its_status_assertion_on_the_request_under_test(env):
    with pytest.raises(ExpectationFailed, match="create_notes/status"):
        run_flow(fake.App(status=500))
    assert record.current().under_test["status"] == 500  # classify: PRODUCT_ERROR


class _Forgets(list):
    def append(self, x):  # the create answers 201, but the app keeps nothing
        pass


def test_no_create_request_and_no_read_back_are_assertions(env):
    no_save = {"name": "create_notes", "steps": ACTIONS[:-1] + FLOW["steps"][-2:]}
    with pytest.raises(ExpectationFailed, match="create_notes/request"):
        run_flow(fake.App(), no_save)
    app = fake.App()
    app.notes = _Forgets()
    with pytest.raises(ExpectationFailed, match="create_notes/read_back"):
        run_flow(app)


def test_main_run_reports_what_running_showed(env, monkeypatch):
    app = fake.App()
    monkeypatch.setattr(ui, "browser", contextlib.contextmanager(lambda: (yield fake.Page(app))))
    out = ui.main("run", json.dumps({"name": "proposal", "steps": ACTIONS}))
    assert out["ok"] and out["unique"].endswith("-ui")
    create = [e for e in out["exchanges"] if e["path"] == "/api/notes"]
    assert create[0]["json"] == {"text": out["unique"]}
    out = ui.main("run", json.dumps({"name": "proposal", "steps": [{"click": {"css": "x"}}]}))
    assert (out["ok"], out["error_type"]) == (False, "FlowError")
    assert ui.main("snapshot", "")["snapshot"].startswith('- textbox "Username"')


# ---- the collector, through pytest (C1, C3) ---------------------------------------------------

CONFTEST = """
import contextlib, os
import httpx
import p7_fakepage as fake
from platform_rt import ui
from platform_rt.api import api

APP = fake.App(os.environ.get("P7_SEED") or None, os.environ.get("P7_SAVE", "Save note"))
ui.browser = contextlib.contextmanager(lambda: (yield fake.Page(APP)))
api.use(httpx.Client(base_url="http://app:8080", transport=httpx.MockTransport(
    lambda r: httpx.Response(200, json={"items": APP.notes}))))
"""


@pytest.mark.parametrize(("variant", "outcome", "origin"), [
    ({}, "passed", None),
    ({"P7_SEED": "page-error"}, "failed", "UI_ERROR"),
    ({"P7_SAVE": "Store note"}, "failed", "BINDING"),
])  # fmt: skip
def test_a_bound_flow_writes_the_same_record_as_any_test(pytester, monkeypatch, env, variant,
                                                         outcome, origin):  # fmt: skip
    here = Path(__file__).parents[1] / "platform_rt"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(here)]))
    for k, v in variant.items():
        monkeypatch.setenv(k, v)
    out = pytester.path / "results.jsonl"
    monkeypatch.setenv("TP_RESULTS", str(out))
    (pytester.path / "p7_fakepage.py").write_text(FAKE)
    pytester.makeconftest(CONFTEST)
    e = gen_ui.entry(FLOW)
    (pytester.path / e["file"]).write_text(e["source"])
    (pytester.path / "broken.ui.yaml").write_text("name: broken\nsteps: [{click: {css: x}}]\n")
    pytester.runpytest_subprocess(
        "-p", "platform_rt.plugin", "-p", "no:cacheprovider", "--continue-on-collection-errors"
    )  # as Stack.pytest runs it
    recs = {r["nodeid"]: r for r in map(json.loads, out.read_text().splitlines())}
    r = recs["create_notes.ui.yaml::create_notes[U:create_notes]"]
    assert (r["outcome"], r["origin"], r["kind"], r["case_id"]) == (outcome, origin, "ui",
                                                                   "U:create_notes")  # fmt: skip
    assert recs["broken.ui.yaml"]["origin"] == "BINDING" and recs["broken.ui.yaml"]["kind"] == "ui"


# ---- the provider (C4) ------------------------------------------------------------------------


def _ctx(flows):
    checks = types.SimpleNamespace(value={"ui": flows} if flows is not None else {})
    return types.SimpleNamespace(profile=types.SimpleNamespace(checks=checks))


def test_each_stored_flow_becomes_a_hashed_bound_entry(monkeypatch):
    built = []
    monkeypatch.setattr(gen_ui, "build_image", lambda: built.append(1))
    stored = FLOW | {"verified": {"op": "POST /api/notes", "read_back": "GET /api/notes"}}
    (e,) = gen_ui.entries(_ctx([stored]), {})
    assert {k: v for k, v in e.items() if k not in ("source", "lock_hash")} == {
        "unit": "U:create_notes", "ac": None, "file": "create_notes.ui.yaml", "status": "BOUND",
        "kind": "ui", "generic": True}  # fmt: skip
    assert "verified" not in e["source"] and e["lock_hash"] == gen_ui.sha256(e["source"])
    assert built == [1]
    assert gen_ui.entries(_ctx([stored]), {"units": ["U1"]}) == []
    assert gen_ui.entries(_ctx(None), {}) == [] and built == [1]  # no flows, no image


def test_a_ui_entry_picks_the_browser_runner(monkeypatch, tmp_path):
    calls = []

    def fake_run(*cmd, **kw):
        calls.append(cmd)
        return "sha256:" + cmd[-1].rsplit(":", 1)[-1] if cmd[1] == "image" else ""

    monkeypatch.setattr(image, "_run", fake_run)
    runner = gen_ui.build_image(tag="tp-runner-browser:p7-dev", base="tp-runner-blackbox:p7-dev")
    build = next(c for c in calls if c[1] == "build")
    assert "BASE=tp-runner-blackbox:p7-dev" in build and build[-1].endswith("images/runner-browser")
    assert image.published_browser() == runner and runner["image"] == "tp-runner-browser:p7-dev"
    ctx = types.SimpleNamespace(strategies=types.SimpleNamespace(
        reach=types.SimpleNamespace(image=lambda v: None)))  # fmt: skip
    assert stage._runner(ctx, [gen_ui.entry(FLOW)]) == runner


# ---- discovery: proposed by the model, verified by running (DR-1) ------------------------------

SPEC = {"paths": {"/api/login": {"post": {}}, "/api/notes": {"get": {}, "post": {}},
                  "/api/notes/{id}": {"get": {}, "delete": {}}, "/api/users": {"post": {}}}}  # fmt: skip
UNIQUE = "tp-discover-a1b2c3-ui"


class Llm:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def call(self, prompt, **kw):
        self.prompts.append(prompt)
        return json.dumps({"steps": self.answers.pop(0)})


def _model_steps(save="Save note"):
    return [{"action": "goto", "path": "/"},
            {"action": "fill", "role": "textbox", "name": "Username", "value": "tpadmin"},
            {"action": "fill", "role": "textbox", "name": "Password",
             "value": "$TP_SECRET_ADMIN_PASSWORD"},
            {"action": "click", "role": "button", "name": "Sign in"},
            {"action": "fill", "role": "textbox", "name": "Note", "value": "$TP_UNIQUE"},
            {"action": "click", "role": "button", "name": save}]  # fmt: skip


def _disc(llm, reads=None, strategy="dockerfile", login=True, page="<!doctype html><html>"):
    """A Discovery stand-in: the attributes ui_flows uses, with scripted HTTP answers."""
    d = types.SimpleNamespace(llm=llm, probes=Probes(), slug="notify", spec=SPEC, emitted=[],
                              cfg={"model": "claude-haiku-5-5", "admin_user": "tpadmin"},
                              values={"reach": {"strategy": strategy},
                                      "auth": {"login": {"path": "/api/login"}} if login else {}},
                              prov={}, stack=types.SimpleNamespace(egress=lambda image, env:
                                  contextlib.nullcontext(("it3-p7_run", BASE))))  # fmt: skip
    answers = {"/": page} | (reads if reads is not None else {"/api/notes": {"items": [UNIQUE]}})

    def http(method, path, headers=None):
        d.probes.log.append({"n": len(d.probes.log), "kind": "http", "key": f"{method} {path}"})
        return len(d.probes.log) - 1, 200 if path in answers else 404, answers.get(path)

    d.http, d.auth_headers = http, lambda: {"Authorization": "Bearer x"}
    d.emit = lambda ev, **kw: d.emitted.append(kw)
    d.mark = lambda c, k, s, layer, n: d.prov.setdefault(c, {}).__setitem__(
        k, {"source": s, "layer": layer, "probe": n})  # fmt: skip
    return d


def _ran(ok=True, error_type=None, error=None, snapshot=None, created=True):
    ex = [{"kind": "ui", "method": "UI", "path": "goto /", "status": 0, "json": None}]
    if created:
        ex.append({"method": "POST", "path": "/api/notes", "status": 201,
                   "json": {"text": UNIQUE}})  # fmt: skip
    return {"ok": ok, "error_type": error_type, "error": error, "unique": UNIQUE,
            "exchanges": ex, "observed": {"snapshot": snapshot} if snapshot else {}}  # fmt: skip


@pytest.fixture
def runner(monkeypatch):
    """Scripted browser-runner answers, in order; records each docker command and its stdin."""
    script, cmds = [], []

    def fake_run(cmd, input=None, env=None, **kw):
        cmds.append((cmd, input, env))
        return types.SimpleNamespace(stdout=json.dumps(script.pop(0)) + "\n", stderr="")

    monkeypatch.setattr(ui_flows.subprocess, "run", fake_run)
    monkeypatch.setattr(gen_ui, "build_image", lambda: {"digest": "sha256:browser"})
    monkeypatch.setattr(ui_flows.secrets, "get", lambda slug, name: "pw-very-secret")
    return script, cmds


def test_a_proposal_is_repaired_from_where_it_failed_then_verified_and_stored(runner):
    script, cmds = runner
    script += [{"ok": True, "snapshot": '- textbox "Username"\n- button "Sign in"'},
               _ran(False, "LocatorError", 'click button "Save": timeout',
                    '- textbox "Note"\n- button "Save note"', created=False),
               _ran()]  # fmt: skip
    llm = Llm(_model_steps("Save"), _model_steps())
    d = _disc(llm)
    ui_flows.run(d)
    (flow,) = d.ui_flows
    assert flow["name"] == "create_notes"
    assert flow["steps"] == ACTIONS + FLOW["steps"][-2:]
    assert flow["verified"] == {"op": "POST /api/notes", "read_back": "GET /api/notes"}
    assert d.prov["checks"]["ui create_notes"]["source"] == "ai_proposed+verified"
    assert 'button "Sign in"' in llm.prompts[0] and "$TP_UNIQUE" in llm.prompts[0]
    assert "timeout" in llm.prompts[1] and 'button "Save note"' in llm.prompts[1]
    cmd, stdin, env = cmds[1]
    assert cmd[:6] == ["docker", "run", "--rm", "-i", "--network", "it3-p7_run"]
    assert cmd[-4:] == ["python", "-m", "platform_rt.ui", "run"] and "sha256:browser" in cmd
    assert "pw-very-secret" not in json.dumps(cmd) and env["TP_SECRET_ADMIN_PASSWORD"]
    assert json.loads(stdin)["steps"][0] == {"goto": "/"}
    assert ui.parse({"name": flow["name"], "steps": flow["steps"]})  # what the runner will run


def test_a_replay_gives_the_same_flow_without_a_browser(runner):
    script, cmds = runner
    script += [{"ok": True, "snapshot": "- button"}, _ran()]
    d = _disc(Llm(_model_steps()))
    ui_flows.run(d)
    log = [e for e in d.probes.log if e["kind"] == "docker"]
    again = _disc(Llm(_model_steps()))
    again.probes = Probes(replay=log)
    n = len(cmds)
    ui_flows.run(again)
    assert again.ui_flows == d.ui_flows and len(cmds) == n


@pytest.mark.parametrize(("ran", "reads", "why"), [
    (_ran(created=False), None, "no 2xx write carried the typed value"),
    (_ran(), {"/api/notes": {"items": []}}, "no GET read back the typed value"),
])  # fmt: skip
def test_a_flow_is_stored_only_when_the_create_and_its_read_back_are_seen(runner, ran, reads,
                                                                          why):  # fmt: skip
    script, _ = runner
    script += [{"ok": True, "snapshot": "-"}] + [ran] * ui_flows.ROUNDS
    d = _disc(Llm(*[_model_steps()] * ui_flows.ROUNDS), reads)
    ui_flows.run(d)
    assert d.ui_flows == [] and why in d.emitted[-1]["reason"]


def test_a_page_error_while_proposing_is_not_repaired(runner):
    script, _ = runner
    script += [{"ok": True, "snapshot": "-"}, _ran(False, "UiPageError", "TypeError: boom")]
    llm = Llm(_model_steps(), _model_steps())
    d = _disc(llm)
    ui_flows.run(d)
    assert d.ui_flows == [] and len(llm.prompts) == 1 and "UiPageError" in d.emitted[-1]["reason"]


@pytest.mark.parametrize(("kw", "why"), [
    ({"strategy": "external"}, "live system"),
    ({"login": False}, "no password sign-in"),
    ({"page": {"openapi": "3.0.0"}}, "no HTML page"),
])  # fmt: skip
def test_discovery_proposes_no_flow_when_it_cant_or_mustnt(runner, kw, why):
    _, cmds = runner
    d = _disc(kw.pop("llm", Llm()), **kw)
    ui_flows.run(d)
    assert d.ui_flows == [] and why in d.emitted[-1]["reason"] and cmds == []


def test_without_a_model_discovery_proposes_nothing(runner):
    d = _disc(None)
    ui_flows.run(d)
    assert getattr(d, "ui_flows", []) == [] and d.emitted == [] and runner[1] == []
