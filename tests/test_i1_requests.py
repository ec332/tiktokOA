"""I1: the merged branches' requests (integration/001): stand-in command, image and host route."""

from tp.stages.env import blackbox as bb

HTTP = {"name": "http", "kind": "http", "image": "runner", "port": 8000, "journal": "/__journal",
        "command": ["python", "-m", "platform_rt.standin", "8000"], "env": {}}  # fmt: skip


def test_a_standin_runs_its_command_and_gets_a_host_forwarder(tmp_path):
    reach = {"service": "app", "port": 80, "standins": [HTTP]}
    out, _ = bb.rewrite({"services": {"app": {"image": "x:1"}}}, tmp_path, tmp_path / "run", reach,
                        {"tp-standin-http": "tp-runner-blackbox:abc"})  # fmt: skip
    s = out["services"]
    assert s["tp-standin-http"]["image"] == "tp-runner-blackbox:abc"  # `runner`, resolved (R3)
    assert s["tp-standin-http"]["command"] == HTTP["command"]
    fwd = s["tp-forwarder-http"]  # R4: the journal is read from the host through this
    assert fwd["networks"] == ["run", "edge"] and fwd["ports"] == ["127.0.0.1::8000"]
    assert fwd["command"][-1] == "TCP:tp-standin-http:8000"
    assert s["tp-standin-http"]["networks"] == ["run"]  # the stand-in itself stays internal


def test_a_dev_mount_of_the_build_context_is_dropped(tmp_path):
    svc = {"build": ".", "volumes": [".:/home/node/app", "/home/node/app/node_modules",
                                     "./seed.sql:/seed.sql", {"type": "bind", "source": "./", "target": "/x"}]}  # fmt: skip
    out, _ = bb.rewrite({"services": {"app": svc}}, tmp_path, tmp_path / "run",
                        {"service": "app", "port": 3000}, {"app": "tp-x-app:abc"})  # fmt: skip
    vols = out["services"]["app"]["volumes"]
    assert vols[0] == "/home/node/app/node_modules"  # anonymous volumes stay
    assert vols[1].endswith("/run/binds/seed.sql:/seed.sql") and len(vols) == 2  # other binds move


def test_a_named_target_neither_reads_nor_swaps_the_env(tmp_path, monkeypatch):
    """/claim and start_run run on API threads beside a live run: they pass the target, never
    swap TP_TARGET, which the run's own stages read (integration/002, review finding 1)."""
    import shutil

    from tp.core import profile, runner
    from tp.stages.report import build

    d = tmp_path / "other"
    d.mkdir()
    shutil.copy("targets/fastapi-template/profile.yaml", d / "profile.yaml")
    monkeypatch.setenv("TP_TARGET", "the-live-run")
    monkeypatch.setattr(profile, "selected", None)  # any env swap here would now crash
    assert profile.load(t=str(d)).slug == "other"
    assert build.run_facts(None, str(d))["target"]["slug"] == "other"
    assert runner._pin_target({"profile": str(d)}) == str(d.resolve())
    assert profile.os.environ["TP_TARGET"] == "the-live-run"


def test_a_servers_base_the_paths_lack_is_put_in_front_of_them():
    """RealWorld: `servers` /api, paths /articles; requests go out as the paths say (P4 R2)."""
    from tp.stages.discover import spec_base

    spec = {"servers": [{"url": "https://api.example.test/api"}], "paths": {"/articles": {}}}
    assert spec_base.with_base(spec) == {"paths": {"/api/articles": {}}}  # the host is dropped too
    full = {"servers": [{"url": "/api"}], "paths": {"/api/articles": {}}}
    assert spec_base.with_base(full) is full  # already there: unchanged
    assert spec_base.with_base({"paths": {"/a": {}}}) == {"paths": {"/a": {}}}  # no base


def test_the_pack_spec_is_the_vendored_one_with_its_base():
    from tp.stages.discover import discovery, spec_base

    rel, spec = spec_base.pack_spec("realworld")
    assert rel == "realworld/spec/openapi.yml" and "servers" not in spec
    assert discovery.common_base(list(spec["paths"])) == "/api"


class _D:
    """A scripted discovery (K5) for the stand-in helpers: a login answer and a model."""

    def __init__(self, answer=None, src=None):
        self.values = {"auth": {"login": {"path": "/api/auth/signin", "json": True, "body": {}}}}
        self.answer, self.src, self.calls, self.emitted = answer, src, [], []
        self.cfg = {"model": "claude-haiku-5-5"}
        self.llm = self

    def resolve_body(self, b):
        return b

    def http(self, method, path, **kw):
        self.calls.append((method, path))
        return 1, 200, {"user": {"name": "users/7", "username": "tpadmin"}, "accessToken": "x" * 40}

    def call(self, prompt, **kw):
        self.prompt = prompt
        return self.answer

    def emit(self, kind, **kw):
        self.emitted.append((kind, kw))


def test_a_registration_path_is_filled_from_the_principals_own_login_answer():
    """Memos registers webhooks at POST /api/users/{user}/webhooks (integration/002)."""
    from tp.stages.discover import dependencies as dep

    got = dep.concrete(_D(), "/api/users/{user}/webhooks")
    assert got == ["/api/users/7/webhooks", "/api/users/tpadmin/webhooks"]  # running picks one
    assert dep.concrete(_D(), "/api/hooks") == ["/api/hooks"]  # no parameter: no login call


def test_a_refused_registration_asks_the_model_for_the_guard_once(tmp_path):
    import json
    import subprocess

    from tp.stages.discover import dependencies as dep

    (tmp_path / "main.go").write_text('viper.SetEnvPrefix("memos")\nflags.StringSlice('
                                      '"webhook-private-network-allowlist", nil, "")\n')  # fmt: skip
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    answer = json.dumps({"candidates": [{"env": "MEMOS_WEBHOOK_PRIVATE_NETWORK_ALLOWLIST",
                                         "kind": "http", "quote": "SetEnvPrefix"}]})  # fmt: skip
    cfg = dep.config()
    d = _D(answer, tmp_path)
    refused = [{"via": "spec", "refused": True}]
    assert dep.guard_names(d, cfg, refused, []) == ["MEMOS_WEBHOOK_PRIVATE_NETWORK_ALLOWLIST"]
    assert "webhook-private-network-allowlist" in d.prompt  # the model sees the code's lines
    assert dep.guard_names(d, cfg, refused, ["ALREADY_SET"]) == []  # a guard is set: no call
    assert dep.guard_names(d, cfg, [{"via": "spec"}], []) == []  # nothing refused: no call


def test_an_unnamed_element_and_a_binary_body():
    """Memos: a rich-text editor with no accessible name, and protobuf over Connect
    (integration/002). A binary body is never kept, so a password in one can't leak."""
    import types

    import pytest

    from platform_rt import record, ui

    ok = {
        "name": "create_memo",
        "steps": [{"goto": "/"}, {"fill": {"role": "textbox", "value": "x"}}],
    }
    assert ui.parse(ok) is ok
    with pytest.raises(ui.FlowError):
        ui.parse({"name": "f", "steps": [{"fill": {"role": "textbox", "name": " ", "value": "x"}}]})
    record.start("f.ui.yaml::f[U:f]", "U:f")
    body = b"\x0a\x18tp-r1-abc-ui\x12\x09pw-secret"
    req = types.SimpleNamespace(method="POST", url="http://memos.test/memos.api.MemoService/CreateMemo",
                                headers={"content-type": "application/proto"}, post_data=body.decode(),
                                post_data_buffer=body)  # fmt: skip
    resp = types.SimpleNamespace(request=req, status=200, headers={}, body=lambda: b"")
    got = ui._http(resp, "tp-r1-abc-ui")
    assert got["json"] == "<binary, 25 bytes; carries tp-r1-abc-ui>"
    assert "pw-secret" not in str(got)


def test_the_read_back_tries_the_reads_named_like_the_create_first():
    """Memos creates over RPC (CreateMemo) and reads over REST (/api/memos), the 7th GET."""
    from tp.stages.discover import ui_flows

    spec = {"paths": {f"/api/a{i}": {"get": {}} for i in range(6)} | {"/api/memos": {"get": {}}}}
    asked = []

    class D:
        def http(self, method, path, headers=None):
            asked.append(path)
            return 1, 200, {"memos": [{"content": "tp-u-ui"}]} if path == "/api/memos" else {}

        def auth_headers(self):
            return {}

        def mark(self, *a):
            pass

    D.spec = spec
    out = {"unique": "tp-u-ui", "exchanges": [{"method": "POST", "status": 200, "path":
           "/memos.api.MemoService/CreateMemo", "json": "<binary, 25 bytes; carries tp-u-ui>"}]}  # fmt: skip
    flow, _ = ui_flows.verify(D(), [{"goto": "/"}], out)
    assert asked == ["/api/memos"] and flow["steps"][-1] == {
        "read_back": {"path": "/api/memos", "contains": "$TP_UNIQUE"}}  # fmt: skip
