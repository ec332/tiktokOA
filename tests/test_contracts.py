"""W0 contracts C2 (labels) and C10 (the notify fixture), docs/iteration3/W0-contracts.md.

The fixture is synthetic: a known-answer app for the new checks (DR-8). Here it runs as a local
process. The docker test builds its image, the way P2 will reach it."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
import yaml

from tp.core import labels

NOTIFY = Path(__file__).parent / "fixtures" / "sut" / "notify"

# ---- C2: labels ---------------------------------------------------------------------------------


def test_basis_says_what_a_verdict_rests_on():
    standin = {"path": "http://tp-standin-hook:8080/__journal", "status": 200}
    assert labels.basis({"kind": "suite", "exchanges": []}) == "pre-generated"
    assert labels.basis({"kind": "generic", "fault": {"standin": "hook"}}) == "simulated"
    assert labels.basis({"kind": "generic", "exchanges": [standin]}) == "simulated"
    assert labels.basis({"kind": "case", "exchanges": [{"path": "/api/notes"}]}) == "real"
    assert labels.basis({"nodeid": "t.py::x"}) == "real"  # a record from before platform_rt 0.9
    assert {"real", "simulated", "pre-generated"} <= set(labels.LABELS)  # the M0 vocabulary


def _profile(strategy: str, **value):
    return SimpleNamespace(reach=SimpleNamespace(strategy=strategy, value=value))


def test_isolation_levels():
    assert labels.isolation(_profile("container")) == 0
    assert labels.isolation(_profile("container", standins=[{"name": "hook"}])) == 1
    assert labels.isolation(_profile("external")) == 2


# ---- C10: the notify fixture --------------------------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    received: ClassVar[list] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).received.append(json.loads(body or b"null"))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def receiver():
    _Recorder.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", _Recorder.received
    server.shutdown()


def _serve(app_dir: Path, notify_url: str):
    port = _port()
    env = {**os.environ, "PORT": str(port), "NOTIFY_URL": notify_url}
    env.pop("SMTP_HOST", None)
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=app_dir, env=env,
                            stderr=subprocess.DEVNULL)  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/openapi.json", timeout=0.2)
            return proc, base
        except httpx.TransportError:
            time.sleep(0.05)
    proc.kill()
    raise RuntimeError("the fixture didn't start")


def _wait(received: list, n: int = 1) -> list:
    for _ in range(60):
        if len(received) >= n:
            break
        time.sleep(0.05)
    return received


def _create_note(base: str) -> dict:
    tok = httpx.post(base + "/api/users", json={"username": "u1", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert httpx.get(base + "/api/notes").status_code == 401  # anonymous reads are refused
    note = httpx.post(base + "/api/notes", json={"text": "tp-run-x note"}, headers=h).json()
    assert httpx.get(f"{base}/api/notes/{note['id']}", headers=h).json() == note
    return note


def test_the_fixture_serves_its_spec_and_notifies_on_create(receiver):
    url, received = receiver
    proc, base = _serve(NOTIFY, url)
    try:
        spec = httpx.get(base + "/openapi.json").json()
        assert {"/api/users", "/api/notes", "/api/invite"} <= set(spec["paths"])
        note = _create_note(base)
        assert _wait(received) == [{"event": "note.created", "note": note}]
        # no SMTP_HOST: a graceful 503, not a crash
        tok = httpx.post(base + "/api/login", json={"username": "u1", "password": "pw"})
        h = {"Authorization": f"Bearer {tok.json()['token']}"}
        assert (
            httpx.post(base + "/api/invite", json={"email": "a@b.c"}, headers=h).status_code == 503
        )
    finally:
        proc.kill()


def test_every_seed_applies_and_is_listed_with_its_answer(tmp_path):
    answers = yaml.safe_load((NOTIFY / "seeds" / "answers.yaml").read_text())
    patches = sorted(p.stem for p in (NOTIFY / "seeds").glob("*.patch"))
    assert patches == sorted(answers["seeds"]) and answers["clean"] == []
    for name in patches:
        work = tmp_path / name
        work.mkdir()
        (work / "app.py").write_text((NOTIFY / "app.py").read_text())
        subprocess.run(["git", "apply", str(NOTIFY / "seeds" / f"{name}.patch")], cwd=work,
                       check=True)  # fmt: skip
        assert "SEEDED" in (work / "app.py").read_text()


def test_the_webhook_seed_is_a_known_answer(tmp_path, receiver):
    """What P6's dependency_called must see: the clean app sends the note; the seed doesn't."""
    url, received = receiver
    (tmp_path / "app.py").write_text((NOTIFY / "app.py").read_text())
    subprocess.run(["git", "apply", str(NOTIFY / "seeds" / "webhook-drops-body.patch")],
                   cwd=tmp_path, check=True)  # fmt: skip
    proc, base = _serve(tmp_path, url)
    try:
        _create_note(base)
        assert _wait(received) == [{}]  # the note never reaches the receiver
    finally:
        proc.kill()


@pytest.mark.docker
def test_the_fixture_image_builds_and_serves():
    tag = "tp-notify-fixture:w0-dev"  # C11
    subprocess.run(["docker", "build", "-q", "-t", tag, str(NOTIFY)], check=True,
                   capture_output=True, timeout=600)  # fmt: skip
    port = _port()
    cid = subprocess.run(["docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{port}:8080", tag],
                         check=True, capture_output=True, text=True).stdout.strip()  # fmt: skip
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(base + "/openapi.json", timeout=0.5).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        assert _create_note(base)["text"] == "tp-run-x note"
    finally:
        subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)
