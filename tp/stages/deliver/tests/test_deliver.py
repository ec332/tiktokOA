"""Deliver with scripted git/gh (autonomy/001 F3-F6): PR vs issue, and both reproductions run."""

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tp.core import artifacts, sut
from tp.stages.deliver import stage as dl

OWN = {"id": "D-reference/C-8", "variant": "reference", "ac": "C-8", "kind": "real",
       "class": "PRODUCT_DEFECT", "cases": ["TC-C8-1"],
       "observed": {"method": "PUT", "path": "/api/v1/items/42", "query": {},
                    "json": {"title": "x"}, "status": 200},
       "failed_assertions": [{"id": "TC-C8-1/status", "ok": False, "expected": "403", "actual": 200}],
       "suggestion": {"applies": True, "explanation": "check the owner", "patch": "P"}}  # fmt: skip
CRIT = {"id": "C-8", "title": "Only the owner can update an item",
        "routes": ["PUT /api/v1/items/{id}"]}  # fmt: skip


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(dl, "_run_name", lambda ex: "r-0007")
    monkeypatch.setenv("TP_PUBLISH", "1")


def inputs(defect=OWN, verdict="FIXED_CONFIRMED"):
    fix = artifacts.put("fix_patch", {"patch": "P"})
    art = lambda body: NS(body=body, id="sha256:x")
    return {"triage": art({"defects": [defect], "findings": []}),
            "retest": art({"retests": [{"defect": defect["id"], "verdict": verdict, "fix_patch": fix.id,
                                        "fail_to_pass": [], "pass_to_pass": {"regressions": []}}]}),
            "bound_tests": art({"tests": [{"unit": "TC-C8-1", "file": "test_c8.py", "ac": "C-8",
                                           "status": "BOUND", "source": "def test(): pass\n"}]}),
            "gate_report": art({"rows": [{"unit": "TC-C8-1", "case_id": "TC-C8-1", "kills": 0,
                                          "seeded": None, "status": "CANDIDATE_DEFECT",
                                          "stability": "STABLE@K5", "outcome": "failed"}]}),
            "cases": art({"cases": [{"id": "TC-C8-1", "technique": "error_guessing"}]}),
            "spec": art({"criteria": [CRIT], "sut": {"ref": "abc"}}),
            "run_options": art({})}  # fmt: skip


def ctx(live=False, scheme="Token"):
    p = NS(
        auth=NS(value={"strategy": "token_header", "scheme": scheme}),
        spec=NS(value={"strategy": "served", "openapi": "/api/schema/"}),
    )
    return NS(review=NS(execution_id=1), strategies=NS(reach=NS(live=live)), profile=p,
              emit=lambda *a, **kw: None)  # fmt: skip


def deliver(c, ins):
    res = dl.DeliverStage().run(c, ins)
    assert res.status == "succeeded", res.reason
    return res.outputs["delivery"]["defects"]


def test_external_finding_is_an_issue_and_its_curl_runs_with_token_from_the_env(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(dl, "sh", lambda *a, **k: pytest.fail("no git/gh on an external target"))
    (d,) = deliver(ctx(live=True), inputs())
    assert "pr" not in d and d["score"]["rules"]["not_external"] is False
    assert "hard rule not met: not_external" in d["issue"]["why"]
    rep = d["issue"]["repro"]
    assert "$TOKEN" in rep["curl"] and "--include-path '/api/v1/items/{id}'" in rep["schemathesis"]
    assert "--include-method PUT" in rep["schemathesis"]
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers["content-length"])
            seen.update(auth=self.headers["Authorization"], path=self.path, body=self.rfile.read(n))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    script = tmp_path / "r.sh"
    script.write_text(Path(d["issue"]["file"].replace(".md", ".sh")).read_text())
    env = {
        "PATH": "/usr/bin:/bin",
        "BASE_URL": f"http://127.0.0.1:{srv.server_port}",
        "TOKEN": "s3",
    }
    out = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, check=True)
    srv.shutdown()
    assert "200" in out.stdout.splitlines()[0]
    assert seen == {"auth": "Token s3", "path": "/api/v1/items/42", "body": b'{"title": "x"}'}


def test_owned_fork_and_confirmed_fix_opens_a_draft_pr(monkeypatch):
    monkeypatch.setattr(sut, "target", lambda: {"repo": "/r", "branch": "seeded"})
    monkeypatch.setattr(dl, "owned_fork", lambda repo: "me/fork")
    monkeypatch.setattr(dl.contract, "SERVICES", {"env": NS(harness_files=dict)})
    calls = []
    monkeypatch.setattr(dl, "open_pr", lambda *a: calls.append(a) or {"url": "u", "repo": "me/fork",
                                                                      "date": "d"})  # fmt: skip
    (d,) = deliver(ctx(), inputs())
    assert d["pr"]["url"] == "u" and d["score"]["parts"] == {"permission_bypass": 3, "stable": 1.0}
    fork, _t, ref, branch, _run, chosen, _src, _h, patch, title, body = calls[0]
    assert (fork, ref, branch, patch) == ("me/fork", "abc", "tp/fix-r-0007-c-8", "P")
    assert [c["file"] for c in chosen] == ["test_c8.py"] and title == CRIT["title"]
    assert "AI model" in body and "check the owner" in body


@pytest.mark.parametrize("change,rule", [
    ({"verdict": "NOT_FIXED"}, "fixed_confirmed"),
    ({"publish": None}, "publishing_on"),  # autonomy/001 F11: off unless TP_PUBLISH=1
    ({"defect": OWN | {"class": "SPEC_MISMATCH"}}, "product_defect"),
])  # fmt: skip
def test_no_pr_without_a_confirmed_fix_or_on_spec_mismatch(monkeypatch, change, rule):
    monkeypatch.setattr(sut, "target", lambda: {"repo": "/r", "branch": "seeded"})
    monkeypatch.setattr(dl, "owned_fork", lambda repo: "me/fork")
    monkeypatch.setattr(dl, "open_pr", lambda *a: pytest.fail("no PR"))
    if change.pop("publish", 0) is None:
        monkeypatch.delenv("TP_PUBLISH")
    (d,) = deliver(ctx(), inputs(**change))
    assert f"hard rule not met: {rule}" in d["issue"]["why"]


def test_a_low_score_is_an_issue_even_with_every_rule_met(monkeypatch):
    monkeypatch.setattr(sut, "target", lambda: {"repo": "/r", "branch": "seeded"})
    monkeypatch.setattr(dl, "owned_fork", lambda repo: "me/fork")
    monkeypatch.setattr(dl, "open_pr", lambda *a: pytest.fail("no PR"))
    gap = OWN | {"failed_assertions": [{"id": "x/status", "expected": "422", "actual": 201}]}
    (d,) = deliver(ctx(), inputs(defect=gap))
    assert d["score"]["score"] == 1.0 and d["issue"]["why"] == ["score 1.0 below the threshold 3"]


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_open_pr_commits_the_fix_on_the_tests_and_pushes_to_the_fork(monkeypatch, tmp_path):
    fork, repo = tmp_path / "fork.git", tmp_path / "clone"
    git("init", "--bare", "-q", str(fork), cwd=tmp_path)
    git("clone", "-q", str(fork), str(repo), cwd=tmp_path)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "app.py").write_text("ok = False\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "seed", cwd=repo)
    git("push", "-q", "origin", "HEAD:seeded", cwd=repo)
    ref = git("rev-parse", "HEAD", cwd=repo).strip()

    def fake_export(repo_, ref_, branch, *a):
        git("branch", branch, ref_, cwd=repo_)
        return {"tests": ["test_c8.py"]}

    monkeypatch.setattr(dl.export, "export", fake_export)
    real, gh = dl.sh, []
    monkeypatch.setattr(dl, "sh", lambda a, **k: gh.append(a) or "https://pr/1" if a[0] == "gh"
                        else real(a, **k))  # fmt: skip
    patch = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-ok = False\n+ok = True\n"
    out = dl.open_pr("me/fork", {"repo": str(repo), "branch": "seeded"}, ref, "tp/fix-1", "r-1",
                     [], {}, {}, patch, "Owner only", "body")  # fmt: skip
    assert out["url"] == "https://pr/1" and out["repo"] == "me/fork"
    assert git("show", "tp/fix-1:app.py", cwd=fork) == "ok = True\n"  # pushed, fix on top
    assert gh[0][:3] == ["gh", "pr", "create"] and "--draft" in gh[0]
    assert gh[0][gh[0].index("--base") + 1] == "seeded"


def test_a_failing_family_check_counts_as_property_evidence(monkeypatch):
    monkeypatch.setattr(sut, "target", lambda: None)
    fam = "G:auth:PUT /api/v1/items/{id}"
    ins = inputs(defect=OWN | {"cases": [fam]})
    ins["gate_report"].body["rows"] = [{"unit": fam, "case_id": fam, "kills": 0, "seeded": None}]
    (d,) = deliver(ctx(), ins)
    assert d["score"]["parts"] == {"permission_bypass": 3, "plus_planted_bug_or_property": 1.5}


def test_a_spec_mismatch_finding_on_an_external_target_is_an_issue(monkeypatch):
    """Triage keeps spec/server disagreements as findings; deliver drafts them as issues
    (seen live: r-0034 on Paperless had 3 findings, 0 defects, and drafted nothing)."""
    monkeypatch.setattr(dl, "sh", lambda *a, **k: pytest.fail("no git/gh on an external target"))
    cid = "G:properties:GET /api/correspondents/"
    ins = inputs()
    ins["triage"].body = {"defects": [], "findings": [
        {"id": f"F-reference/{cid}", "ac": cid, "case_id": cid, "class": "SPEC_MISMATCH",
         "variant": "reference", "observed": {
             "request": "GET /api/correspondents/", "status": 404, "error": "Undocumented status code",
             "curl": "curl -X GET -H 'Authorization: [Filtered]' 'http://proxy:8000/api/correspondents/?page=7'"}},
        {"id": "F-x", "ac": cid, "case_id": cid, "class": "FLAKY_TEST", "variant": "reference"}]}  # fmt: skip
    (d,) = deliver(ctx(live=True), ins)
    rep = d["issue"]["repro"]
    assert '"$BASE_URL/api/correspondents/?page=7"' in rep["curl"] and "$TOKEN" in rep["curl"]
    assert "--include-path /api/correspondents/" in rep["schemathesis"]
    assert "hard rule not met: not_external" in d["issue"]["why"]
    assert "> Undocumented status code" in d["issue"]["markdown"]
