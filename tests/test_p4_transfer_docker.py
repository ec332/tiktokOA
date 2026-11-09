"""P4's done-when on the notify fixture pair (docs/status/domains/001 D3-D5, D15). SYNTHETIC: the
two "implementations" are the notify fixture (C10) and its `webhook-drops-body` seed committed
as a second repo. Run A binds the fixed families from a pre-generated, empty spec and cases (no
model: K5), then runs gate, env, execute, triage and report on the clean build. Transfer runs
the same sealed tests, by hash, on the seeded build. The comparison must show exactly the seeded
disagreement: a SPEC_MISMATCH on the seed, with the clean build as witness (DR-5, DR-6).

Docker: the stacks are compose projects `tp_bb_it3_p4_*` (C11), removed at the end."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from tp.cli import main
from tp.core import artifacts, profile, review, runner, transfer
from tp.stages.env import blackbox as bb

pytestmark = pytest.mark.docker
NOTIFY = Path(__file__).parent / "fixtures" / "sut" / "notify"
CALLED = "G:dependency_called:POST /api/notes"
CREDS = {"username": "tpadmin", "password": {"secret": "admin_password"}}
WEBHOOK = {"name": "http", "kind": "http", "via": "env", "key": "NOTIFY_URL",
           "trigger": "POST /api/notes", "setup": None, "status": "wired", "evidence": None,
           "reason": "POST /api/notes left a line in tp-standin-http's journal"}  # fmt: skip
# The values discovery finds on the fixture (domains/001 D4), with the stand-in and wiring that
# P6's discovery adds (standins/001 D3, D6), and the sign-in route as the login.
VALUES = {
    "reach": {"strategy": "container", "ref": "v1", "service": "app", "port": 8080, "env": {},
              "build": {"dockerfile": "Dockerfile", "context": ".", "port": 8080},
              "health": "/openapi.json",
              "standins": [{"name": "http", "kind": "http", "image": "runner", "port": 8000,
                            "journal": "/__journal",
                            "command": ["python", "-m", "platform_rt.standin", "8000"],
                            "env": {"NOTIFY_URL": "http://tp-standin-http:8000/notify_url"}}]},
    "spec": {"strategy": "served", "openapi": "/openapi.json", "base": "/api"},
    "auth": {"strategy": "token_header", "scheme": "Bearer", "check": "/api/notes",
             "login": {"path": "/api/login", "body": CREDS, "token_field": "token", "json": True},
             "bootstrap": {"path": "/api/users", "body": CREDS, "json": True}},
    "data": {"strategy": "spec_derived", "id_field": "id", "id_style": "integer", "paging": None,
             "read_back_timeout_s": 10},
    "state": {"strategy": "namespace"},
    "mutation": {"strategy": "none", "reason": "black-box reach"},
    "safety": {"strategy": "unrestricted"},
    "checks": {"strategy": "pytest_cases", "dependencies": [WEBHOOK],
               "families": {"per_family": 5, "examples": 10, "rate": 5, "exclude": [],
                            "dependencies": [WEBHOOK]}},
}  # fmt: skip


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)  # fmt: skip


def implementation(data: Path, name: str, seed: str | None = None) -> str:
    """A repo of the fixture (with the seed committed) and its profile -> the profile folder."""
    repo = data / "repos" / name
    shutil.copytree(NOTIFY, repo, ignore=shutil.ignore_patterns("seeds"))
    if seed:
        subprocess.run(["patch", "-s", "-p1", "-d", str(repo), "-i",
                        str(NOTIFY / "seeds" / f"{seed}.patch")], check=True)  # fmt: skip
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)
    _git(repo, "tag", "v1")
    target = data / "targets" / name
    target.mkdir(parents=True)
    raw = {"schema_version": 1, "target": {"name": name, "repo": str(repo), "type": "api"},
           "commit_tested": "v1",
           **{c: {"source": "override", "value": v} for c, v in VALUES.items()}}  # fmt: skip
    (target / "profile.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return str(target)


def run_a(target: str) -> int:
    """The full pipeline from generate on: the families need no criteria and no cases (D5)."""
    opts = {"profile": target, "stops": review.FOCUSED_STOPS, "gate": {"k": 2}, "k": 2}
    run = runner.start_run("live", options=opts)
    label = ["pre-generated"]  # hand-written here, not model output (R5)
    spec = artifacts.put("spec", {"criteria": [], "decisions": []}, labels=label)
    cases = artifacts.put("cases", {"cases": [], "signoff": {"signed": True, "hash": "none",
                                                             "approved": 0, "lines": 0}},
                          labels=label)  # fmt: skip
    transfer.seed(run, {"spec": {"inputs": {}, "outputs": {"spec": spec.id}},
                        "cases": {"inputs": {"spec": spec.id}, "outputs": {"cases": cases.id}}})  # fmt: skip
    return run


def test_the_same_sealed_tests_on_both_builds_show_exactly_the_seeded_disagreement(data):
    bb._STACKS.clear()
    clean = implementation(data, "it3-p4-notify-clean")
    seeded = implementation(data, "it3-p4-notify-drops-body", "webhook-drops-body")
    try:
        a = run_a(clean)
        ra = runner.advance(a)
        assert {e["stage"]: e["status"] for e in ra["executions"]}["execute"] == "succeeded", ra
        b = transfer.start(a, seeded)
        rb = runner.advance(b)
        assert {e["stage"]: e["status"] for e in rb["executions"]}["execute"] == "succeeded", rb
        ha, hb = runner.heads(a), runner.heads(b)
        bound = ha["bound_tests"]
        assert hb["bound_tests"] == bound  # the same artifact: the same hashes (DR-5)
        for h in (ha, hb):  # and those exact tests are what ran
            assert artifacts.get(h["gate_report"]).body["bound_tests"] == bound
        tests = artifacts.get(bound).body["tests"]
        assert [t["kind"] for t in tests] == ["generic"] and tests[0]["lock_hash"]
        m = transfer.matrix([a, b])
        rows = {r["case"]: r for r in m["rows"]}
        mismatched = [r["case"] for r in m["rows"] if r["class"] == "SPEC_MISMATCH"]
        assert mismatched == [CALLED], transfer.markdown(m)
        row = rows[CALLED]
        assert (row["failing"], row["witnesses"]) == ([f"r-{b:04d}"], [f"r-{a:04d}"])
        assert row["request"] == "POST /api/notes"
        out = CliRunner().invoke(main.app, ["compare", f"r-{a:04d}", f"r-{b:04d}"])
        assert out.exit_code == 0 and f"SPEC_MISMATCH on r-{b:04d}; witness r-{a:04d}" in out.output
        print(out.output)  # the matrix, for the status record
    finally:
        for t in (clean, seeded):
            with profile.selected(t):
                bb.Stack(profile.load()).down()
        images = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                                check=False, capture_output=True, text=True).stdout.split()  # fmt: skip
        gone = [i for i in images if "it3_p4_notify" in i or "it3-p4-notify" in i]
        if gone:
            subprocess.run(["docker", "image", "rm", "-f", *gone], check=False, capture_output=True)
