"""Stage 10 — Deliver (autonomy/001 F3–F6, F11): after the retest, each real defect becomes a
draft PR on a fork we own (tests + retest-proven fix) or an issue draft with a curl and a
Schemathesis reproduction. No LLM: the PR score and the hard rules decide."""

import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from tp.core import artifacts, contract, db, priority, sut
from tp.core.contract import StageResult, register
from tp.stages.report import export

ISSUE_CLASSES = {"PRODUCT_DEFECT", "INTERMITTENT_PRODUCT", "SPEC_MISMATCH"}  # product side
WRITE = ("ADMIN", "MAINTAIN", "WRITE")
DRAFTED = "_Drafted by an automated test platform; cases and tests were written with an AI model._"
DISCLOSURE = (DRAFTED[:-1] + " The fix was proposed by an AI model (Claude Haiku) and accepted only "
              "because the failing tests below pass after it, 5 times out of 5, with the same test "
              "hashes. Please review it as you would any outside contribution._")  # fmt: skip


def sh(args: list[str], cwd: Path | None = None, stdin: str | None = None) -> str:
    """Every git/gh call goes through here (tests script it)."""
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(args, cwd=cwd, input=stdin, capture_output=True, text=True, check=False,
                       env=env, timeout=300)  # fmt: skip
    if r.returncode:
        raise export.ExportError(
            f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:400]}"
        )
    return r.stdout.strip()


def owned_fork(repo: str) -> str | None:
    """`owner/name` of the repo's origin if it is a GitHub fork we can write to, else None."""
    try:
        url = sh(["git", "-C", repo, "remote", "get-url", "origin"])
        info = json.loads(sh(["gh", "repo", "view", url, "--json",
                              "nameWithOwner,isFork,viewerPermission"]))  # fmt: skip
    except export.ExportError, ValueError, FileNotFoundError:
        return None
    need_fork = priority.config()["pr"]["only_forks_we_own"]
    ok = info.get("viewerPermission") in WRITE and (info.get("isFork") or not need_fork)
    return info["nameWithOwner"] if ok else None


def prs_today(repo: str) -> int:
    today = datetime.date.today().isoformat()
    bodies = (artifacts.get(a["id"]).body for a in artifacts.list_("delivery", limit=500))
    return sum(1 for b in bodies for d in b.get("defects", [])
               if (d.get("pr") or {}).get("repo") == repo and d["pr"].get("date") == today)  # fmt: skip


def _auth_scheme(p) -> str:
    v = p.auth.value
    return v.get("scheme") or {"basic": "Basic"}.get(v["strategy"], "Bearer")


def repro(defect: dict, criterion: dict | None, p) -> dict:
    """A curl script and a Schemathesis command; the secret is always `$TOKEN` (master §7)."""
    o = defect.get("observed") or {}
    auth = f"Authorization: {_auth_scheme(p)} $TOKEN"
    q = (
        ("?" + "&".join(f"{k}={v}" for k, v in o.get("query", {}).items()))
        if o.get("query")
        else ""
    )
    curl = ["#!/bin/sh", "# BASE_URL and TOKEN come from the environment", 'set -eu',
            f'curl -sS -i -X {o.get("method", "GET")} "$BASE_URL{o.get("path", "/")}{q}" \\',
            f'  -H "{auth}"' + (" \\" if o.get("json") is not None else "")]  # fmt: skip
    if o.get("json") is not None:
        curl += ['  -H "Content-Type: application/json" \\',
                 f"  --data {shlex.quote(json.dumps(o['json']))}"]  # fmt: skip
    sv = p.spec.value
    schema = (
        f'"$BASE_URL{sv["openapi"]}"' if sv["strategy"] == "served" else shlex.quote(sv["openapi"])
    )
    st = f'schemathesis run {schema} --url "$BASE_URL" -H "{auth}"'
    routes = (criterion or {}).get("routes") or (
        [f"{o['method']} {o['path']}"] if o.get("method") else []
    )
    for r in routes[:1]:
        m, path = r.split(" ", 1)
        st += f" --include-method {m} --include-path {shlex.quote(path)}"
    return {"curl": "\n".join(curl) + "\n", "schemathesis": st,
            "expected": defect.get("failed_assertions", [])}  # fmt: skip


def issue_md(defect: dict, criterion: dict | None, rep: dict, score: dict, why: list[str]) -> str:
    c = criterion or {}
    title = c.get("title") or defect["ac"]
    exp = "; ".join(f"`{a.get('id')}`: expected {a.get('expected')}, got {a.get('actual')}"
                    for a in rep["expected"]) or "see the reproduction"  # fmt: skip
    return "\n".join([
        f"# {title}", "",
        f"**Requirement {defect['ac']}** ({defect['class']}). {exp}.", "",
        *([f"> {defect['detail']}", ""] if defect.get("detail") else []),
        "## Reproduce", "", "```sh", rep["curl"].rstrip(), "```", "",
        "Property check over the same operation:", "", "```sh", rep["schemathesis"], "```", "",
        "## Why an issue, not a PR", "", *[f"- {w}" for w in why], "",
        f"PR score {score['score']} (threshold {score['threshold']}): "
        + " × ".join(f"{k} {v}" for k, v in score["parts"].items()), "", DRAFTED,
    ]) + "\n"  # fmt: skip


def pr_body(defect: dict, criterion: dict | None, retest: dict, tests: list[str], sug: dict) -> str:
    c = criterion or {}
    f2p = retest.get("fail_to_pass", [])
    return "\n".join([
        f"**Requirement {defect['ac']}:** {c.get('title', '')}", "",
        f"**Fix:** {sug.get('explanation', '')}", "",
        "**Evidence:**",
        *[f"- `{r['node']}`: {r['before']} ({r.get('before_dist', '')}) → {r['after']} "
          f"({r.get('after_dist', '')})" for r in f2p],
        (f"- retest verdict {retest['verdict']}; regressions: "
        f"{', '.join(retest.get('pass_to_pass', {}).get('regressions', [])) or 'none'}"), "",
        "**Tests added:** " + ", ".join(f"`{t}`" for t in tests), "", DISCLOSURE,
    ]) + "\n"  # fmt: skip


def open_pr(fork: str, t: dict, ref: str, branch: str, run: str, chosen: list[dict],
            sources: dict, harness: dict, patch: str, title: str, body: str) -> dict:  # fmt: skip
    """Tests commit (the branch export), fix commit on top, push to the fork, draft PR."""
    repo = Path(t["repo"])
    res = export.export(repo, ref, branch, run, chosen, sources, harness)
    wt = Path(tempfile.mkdtemp(prefix="tp-fix-")) / "wt"
    try:
        sh(["git", "-C", str(repo), "worktree", "add", "--quiet", str(wt), branch])
        sh(["git", "apply", "--whitespace=nowarn", "-"], cwd=wt, stdin=patch)
        sh(["git", "commit", "--quiet", "-am", f"Fix: {title}", "-m",
            "Proposed by the test platform's triage; proven by its retest."], cwd=wt)  # fmt: skip
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                       capture_output=True, check=False)  # fmt: skip
        shutil.rmtree(wt.parent, ignore_errors=True)
    # gh's login answers git's credential request; never a terminal prompt (a hang, found live)
    sh(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            "--quiet",
            "origin",
            branch,
        ]
    )
    url = sh(["gh", "pr", "create", "--draft", "--repo", fork, "--base", t["branch"],
              "--head", branch, "--title", title, "--body", body])  # fmt: skip
    return {"url": url.splitlines()[-1], "repo": fork, "branch": branch, "tests": res["tests"],
            "date": datetime.date.today().isoformat()}  # fmt: skip


def from_finding(f: dict) -> dict:
    """A spec/server disagreement (triage keeps it a finding, never a defect) as an issue-only
    defect: `observed` rebuilt from the property check's request and curl."""
    o = f.get("observed") or {}
    method, _, path = (o.get("request") or "GET /").partition(" ")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
        shlex.split(o.get("curl") or "curl x")[-1]).query))  # fmt: skip
    return {"id": f["id"], "kind": "real", "class": f["class"], "ac": f["ac"],
            "cases": [f["case_id"]], "observed": {"method": method, "path": path, "query": query,
                                                  "status": o.get("status")},
            "failed_assertions": [{"id": f"{f['case_id']}/status", "actual": o.get("status"),
                                   "expected": "a status the spec documents"}],
            "detail": o.get("error")}  # fmt: skip


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-").lower()


class DeliverStage:
    name = "deliver"
    version = "1.0.0"
    consumes: dict[str, int] = {"triage": 1, "retest": 1, "bound_tests": 1, "gate_report": 1,  # noqa: RUF012
                                "cases": 1, "spec": 1, "run_options": 1}  # fmt: skip
    produces: dict[str, int] = {"delivery": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        b = {k: a.body for k, a in inputs.items()}
        run = _run_name(ctx.review.execution_id)
        retests = {r["defect"]: r for r in b["retest"].get("retests", [])}
        crit = {c["id"]: c for c in b["spec"].get("criteria", [])}
        rows = b["gate_report"].get("rows", [])
        units = {c["id"]: c for c in b["cases"].get("cases", [])}
        live = getattr(ctx.strategies.reach, "live", False)
        t = sut.target() if not live else None
        fork = owned_fork(t["repo"]) if t else None
        out, folder = [], db.data_dir() / "runs" / run / "issues"
        drift = [from_finding(f) for f in b["triage"].get("findings", [])
                 if f["class"] == "SPEC_MISMATCH" and f.get("variant") == "reference"]  # fmt: skip
        for d in [*b["triage"].get("defects", []), *drift]:
            if d["kind"] != "real" or d["class"] not in ISSUE_CLASSES:
                continue
            mine = [r for r in rows if r.get("case_id") in d["cases"]]
            strong = any(r.get("seeded") or r.get("kills") for r in mine) or any(
                units.get(r.get("unit"), {}).get("technique") == "property"
                or str(r.get("unit") or "").startswith("G:") for r in mine)  # a family check is a property  # fmt: skip
            rt = retests.get(d["id"], {})
            rules = {"product_defect": d["class"] == "PRODUCT_DEFECT",
                     "fixed_confirmed": rt.get("verdict") == "FIXED_CONFIRMED",
                     "not_external": not live, "owned_fork": fork is not None,
                     "under_daily_cap": fork is not None and prs_today(fork)
                     < priority.config()["pr"]["max_per_repo_per_day"],
                     # autonomy/001 F11: nothing is pushed until the user turns publishing on
                     "publishing_on": os.environ.get("TP_PUBLISH") == "1"}  # fmt: skip
            score = priority.pr(d, strong, rules)
            entry = {"defect": d["id"], "ac": d["ac"], "class": d["class"], "score": score}
            c = crit.get(d["ac"])
            if score["pr"]:
                chosen = [x for x in export.candidates(b["bound_tests"], b["gate_report"],
                                                       b["cases"], b["triage"])
                          if x["unit"] in {r.get("unit") for r in mine}]  # fmt: skip
                sources = {
                    x["file"]: x["source"] for x in b["bound_tests"]["tests"] if "source" in x
                }
                title = (c or {}).get("title") or d["ac"]
                try:
                    entry["pr"] = open_pr(
                        fork,
                        t,
                        b["spec"]["sut"]["ref"],
                        f"tp/fix-{run}-{_slug(d['ac'])}",
                        run,
                        chosen,
                        sources,
                        contract.SERVICES["env"].harness_files(),
                        artifacts.get(rt["fix_patch"]).body["patch"],
                        title,
                        pr_body(d, c, rt, [x["file"] for x in chosen], d.get("suggestion") or {}),
                    )
                    ctx.emit("pr_opened", defect=d["id"], url=entry["pr"]["url"])
                    out.append(entry)
                    continue
                except export.ExportError as e:  # the PR failed: fall back to an issue draft
                    entry["pr_failed"] = str(e)
            why = [f"hard rule not met: {k}" for k, v in rules.items() if not v]
            if score["score"] < score["threshold"]:
                why.append(f"score {score['score']} below the threshold {score['threshold']}")
            if "pr_failed" in entry:
                why.append(f"opening the PR failed: {entry['pr_failed']}")
            rep = repro(d, c, ctx.profile)
            md = issue_md(d, c, rep, score, why)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{_slug(d['id'])}.md").write_text(md)
            (folder / f"{_slug(d['id'])}.sh").write_text(rep["curl"])
            entry["issue"] = {"markdown": md, "repro": rep, "file": str(folder / f"{_slug(d['id'])}.md"),
                              "why": why}  # fmt: skip
            ctx.emit("issue_drafted", defect=d["id"])
            out.append(entry)
        return StageResult.succeeded({"delivery": {"defects": out, "fork": fork, "live": live}})


def _run_name(ex_id: int) -> str:
    with db.connect() as c:
        (run_id,) = c.execute("SELECT run_id FROM stage_executions WHERE id=?", (ex_id,)).fetchone()
    return f"r-{run_id:04d}"


STAGE = register(DeliverStage())
