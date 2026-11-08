"""Scan and watch (autonomy/001 F9). A Scan is a focused run over the routes the change touched,
with code-derived behaviour confirmed; `tp watch` starts one whenever the watched branch moves.
Sign-off still waits for a person; everything after it needs no click."""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated

import typer

from tp.core import artifacts, contract, db, review, runner, sut

SCAN_STOPS = [s for s in review.FOCUSED_STOPS if s != "confirm_observed"]


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False,
                       env=os.environ | {"GIT_TERMINAL_PROMPT": "0"})  # fmt: skip
    if r.returncode:
        raise typer.BadParameter(
            f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}"
        )
    return r.stdout.strip()


def changed_routes(changed: dict, routes: list[dict]) -> list[str]:
    """Routes whose handler file the change touched (code_index, FastAPI only)."""
    return sorted({f"{r['method']} {r['path']}" for r in routes if r["file"] in changed})


def scan(target: str | None, repo: str | None, branch: str | None, base: str = "master") -> int:
    """Start a Scan run; returns the run id (it stops at Sign-off like any focused run)."""
    opts: dict = {"stops": SCAN_STOPS, "decisions": {"confirm_observed": "confirm"}}
    if target:
        opts["profile"] = target
    scope: dict = {}
    if repo and branch:
        t = sut.set_target(repo, branch, base)
        routes = changed_routes(t["changed"], contract.resolve("spec").routes())
        scope = {"commit": t["ref"], "subject": t["subject"], "routes": routes}
        if routes:
            opts["routes"] = routes
        brief = f"Scan of {branch} at {t['ref'][:7]}: {t['subject']}"
    else:
        brief = "Scan of the whole verified spec"
    if not opts.get("routes"):  # non-Python target, or a change no route handler holds
        scope["whole_spec"] = "no changed route found: the whole verified spec, ranked by priority"
    opts["scan"] = scope
    req = artifacts.put("requirements", {"kind": "brief", "text": brief}).id
    return runner.start_run("live", inputs={"requirements": req}, options=opts)


def tick(state: Path, repo: str, branch: str, start) -> str | None:
    """One poll: a new head on the fork's branch -> fetch it and start(head). Returns the head
    that started a Scan, or None."""
    head = _git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split("\t")[0]
    if not head:
        raise typer.BadParameter(f"origin has no branch {branch}")
    seen = json.loads(state.read_text()).get("head") if state.exists() else None
    if head == seen:
        return None
    # shortcut: the watched branch must not be checked out in `repo` (fetch can't move it then)
    current = subprocess.run(["git", "-C", repo, "symbolic-ref", "-q", "--short", "HEAD"],
                             capture_output=True, text=True, check=False).stdout.strip()  # fmt: skip
    if current == branch:
        raise typer.BadParameter(f"{branch} is checked out in {repo}, so a fetch can't move it: "
                                 f"run `git -C {repo} checkout --detach`")  # fmt: skip
    _git(repo, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/heads/{branch}")
    start(head)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"head": head, "branch": branch, "at": db.now()}))
    return head


def seconds(every: str) -> int:
    m = re.fullmatch(r"(\d+)([smh])", every)
    if not m:
        raise typer.BadParameter("use e.g. 30s, 5m or 1h", param_hint="--every")
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600}[m[2]]


Target = Annotated[str | None, typer.Option(help="Target profile slug (default TP_TARGET)")]
Repo = Annotated[str | None, typer.Option(help="Local clone of the fork (default: the SUT target)")]
Branch = Annotated[str | None, typer.Option(help="Branch to scan (default: the SUT target's)")]


def _defaults(repo, branch, base):
    t = sut.target() or {}
    return repo or t.get("repo"), branch or t.get("branch"), base or t.get("base") or "master"


def scan_cmd(target: Target = None, repo: Repo = None, branch: Branch = None,
             base: Annotated[str | None, typer.Option()] = None):  # fmt: skip
    """Start a Scan now and drive it to its first stop."""
    run_id = scan(target, *_defaults(repo, branch, base))
    run = runner.advance(run_id)
    typer.echo(f"{run['name']}  {run['status']}")


def watch_cmd(target: Target = None, repo: Repo = None, branch: Branch = None,
              base: Annotated[str | None, typer.Option()] = None,
              every: Annotated[str, typer.Option(help="Poll interval")] = "5m",
              once: Annotated[bool, typer.Option(help="Poll once and exit")] = False):  # fmt: skip
    """Poll the fork with `git ls-remote`; a new head starts a Scan (no daemon: a CLI loop)."""
    repo, branch, base = _defaults(repo, branch, base)
    if not (repo and branch):
        raise typer.BadParameter("no repo/branch: pass --repo and --branch or set a SUT target")
    state = db.data_dir() / "watch" / f"{target or Path(repo).name}.json"
    wait = seconds(every)

    def start(head: str):
        run = runner.advance(scan(target, repo, branch, base))
        typer.echo(f"{head[:7]} -> {run['name']}  {run['status']}")

    while True:
        if tick(state, repo, branch, start) is None:
            typer.echo(f"{time.strftime('%H:%M:%S')} no new commit on {branch}")
        if once:
            return
        time.sleep(wait)
