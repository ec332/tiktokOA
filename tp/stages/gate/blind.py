"""Blind bugs (plan K8; gate design 003 D18): sealed handler mutants nobody sees until reveal.

  tp gate blind seal --spec <id>            draw 3 handler mutants; only their hash is published
  tp gate blind reveal --seal <id> --gate-report <id>   frozen tests vs each sealed mutant

The plain list sits in git-ignored data/blind/sealed.json (honour system); the `blind_seal`
artifact's hash proves the list wasn't changed after the results were seen."""

import json
import random
import secrets
import shutil
from pathlib import Path
from typing import Annotated

import typer

from tp.core import artifacts, contract, db, sut
from tp.core.hashing import sha256
from tp.stages.gate import decide, handlers

N = 3  # one blind bug is a coin flip
TIMEOUT = "15"
app = typer.Typer(no_args_is_help=True, help="Blind bugs: seal, then reveal after the run")


def _path() -> Path:
    return db.data_dir() / "blind" / "sealed.json"


def sealed() -> set[str]:
    """Names the gate must leave out of its pool."""
    return set(json.loads(_path().read_text())["names"]) if _path().exists() else set()


def _pool(env, spec_id: str, out: Path, exclude=frozenset()):
    refs = decide.code_refs(artifacts.get(spec_id).body)
    return handlers.generate(Path(env.code_scope()["src"]), sut.load()["ref"], refs, out, exclude)


def _env():
    contract.load()
    return contract.SERVICES["env"]


@app.command()
def seal(spec: Annotated[str, typer.Option(help="spec artifact id")]):
    if _path().exists():
        raise typer.Exit(f"already sealed: {_path()} (reveal first)")
    env = _env()
    tmp = db.data_dir() / "blind" / "pool"
    _, pool = _pool(env, spec, tmp)
    shutil.rmtree(tmp, ignore_errors=True)  # the pool files hold every name; keep none around
    names = sorted(random.SystemRandom().sample([m["name"] for m in pool], N))
    salt = secrets.token_hex(16)
    _path().write_text(json.dumps({"names": names, "salt": salt, "spec": spec}))
    art = artifacts.put("blind_seal", {"hash": sha256({"names": names, "salt": salt}),
                                       "count": N, "pool": len(pool), "spec": spec},
                        stage="gate", parents=[spec], labels=["real"])  # fmt: skip
    typer.echo(f"sealed {N} of {len(pool)} handler mutants: {art.id}")


@app.command()
def reveal(seal_id: Annotated[str, typer.Option("--seal")],
           gate_report: Annotated[str, typer.Option("--gate-report")]):  # fmt: skip
    s, seal_art = json.loads(_path().read_text()), artifacts.get(seal_id)
    if sha256({"names": s["names"], "salt": s["salt"]}) != seal_art.body["hash"]:
        raise typer.Exit("sealed.json does not match the published hash")
    env, report = _env(), artifacts.get(gate_report).body
    bt = artifacts.get(report["bound_tests"]).body
    work = db.data_dir() / "runs" / f"blind-reveal-{seal_id.removeprefix('sha256:')[:12]}"
    shutil.rmtree(work, ignore_errors=True)
    (work / "tests").mkdir(parents=True)
    for t in bt["tests"]:
        if t["status"] == "BOUND":
            (work / "tests" / t["file"]).write_text(t["source"])
    nodes = sorted(r["node"] for r in report["rows"]
                   if r["outcome"] == "passed" and r["stability"].startswith("STABLE"))  # fmt: skip
    files, pool = _pool(env, s["spec"], work / "mutants")
    env.ensure("reference")
    jobs = [{"key": n, "env": {"MUTANT_UNDER_TEST": n, "TP_TIMEOUT": TIMEOUT},
             "args": ["/work/tests/" + x for x in nodes]} for n in s["names"]]  # fmt: skip
    matrix = env.pytest(work / "tests", work / "matrix", "reference", mounts=files,
                        matrix=jobs)["matrix"]  # fmt: skip
    cells = decide.kills(matrix)
    info = {m["name"]: m for m in pool}
    out = [{**{k: info[n][k] for k in ("name", "file", "line", "diff")},
            "tests": sorted(t for t, row in cells.items() if row.get(n) == "K")}
           for n in s["names"]]  # fmt: skip
    for m in out:
        m["detected"] = bool(m["tests"])
    body = {"names": s["names"], "salt": s["salt"], "hash_ok": True, "mutants": out,
            "detected": sum(m["detected"] for m in out), "count": len(out),
            "gate_report": gate_report, "note": "a sealed mutant may be equivalent: a miss for "
            "every arm alike (gate design 003 D19)"}  # fmt: skip
    art = artifacts.put("blind_reveal", body, stage="gate", parents=[seal_id, gate_report],
                        labels=["real"])  # fmt: skip
    for m in out:
        typer.echo(f"{m['name']} ({m['file']}:{m['line']}): "
                   f"{'DETECTED by ' + str(len(m['tests'])) if m['detected'] else 'missed'}")  # fmt: skip
    typer.echo(art.id)
