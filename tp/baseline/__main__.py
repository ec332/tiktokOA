"""Baseline comparison (plan Part 5; design docs/status/baseline/001).

  python -m tp.baseline naive [--samples 3] [--replay]   -> naive_tests artifact (LLM)
  python -m tp.baseline compare --naive <id>              -> baseline_report + baseline.md

Not a pipeline stage (B9): the runner and `tp stage run` know only the nine stages."""

import json
import subprocess
import time
from pathlib import Path
from typing import Annotated

import typer

from tp.baseline import decide, propose, suite
from tp.core import artifacts, contract, db, llm, sut

VERSION = "1.0.0"
# The platform arm's existing artifacts (B4; retest status 004 handover).
RUN_RECORDS = "sha256:dfb6c25a8cf6f37254e682f70dbae61826df818b3fca48c8acafd83c4176df01"  # x252
CHECK_RECORDS = "sha256:aefea7ec748c16b033f58e7079276fd14c7aa13ebfa6115a8ccb2040e8a29814"  # #469
RETEST = "sha256:91080c4e8a394049bc134b7a475de8992d27aa70d4fb0295825cffc1d50a7acf"  # O-6 fixed
GATE_REPORT = "sha256:e06c537d3d73b52a0c99ee93098d702a3e2133f9e4fa1f88c74f38d256ce7497"

app = typer.Typer(add_completion=False)


def _env():
    contract.load()
    return contract.SERVICES["env"]


def _work(kind: str) -> Path:
    path = db.data_dir() / "runs" / f"baseline-{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True)
    return path


def _source(src: Path, ref: str) -> str:
    """What the naive LLM sees (B5): upstream backend code at the pinned ref, none of our
    patches (they contain settings.BUG_*), plus the test fixtures and helpers, not the tests."""
    files = subprocess.run(["git", "-C", str(src), "ls-tree", "-r", "--name-only", ref,
                            "backend/app", "backend/tests"], check=True, capture_output=True,
                           text=True).stdout.split()  # fmt: skip
    keep = [f for f in files if f.endswith(".py") and "/alembic/" not in f
            and not Path(f).name.startswith("test_")]  # fmt: skip
    show = lambda f: subprocess.run(["git", "-C", str(src), "show", f"{ref}:{f}"], check=True,
                                    capture_output=True, text=True).stdout  # fmt: skip
    return "\n\n".join(f"### {f}\n```python\n{body}```" for f in keep if (body := show(f)))


@app.command()
def naive(samples: int = 3, replay: bool = False, budget: float = 3.0):
    """Arm (a): N independent naive suites, each repaired on reference then pruned (B6-B7)."""
    env, cfg = _env(), sut.load()
    src, ref = Path(env.code_scope()["src"]), cfg["ref"]
    env.ensure("reference")
    gw = llm.Gateway("replay" if replay else "record", llm.anthropic_provider, budget_usd=budget)
    work, source, out = _work("naive"), _source(src, ref), []
    for i in range(1, samples + 1):
        runs = iter(range(1000))
        folder = suite.sut_tests(src, ref, work / f"s{i}", with_tests=False)
        spent = gw.spent_usd
        body = propose.naive(gw, source, folder, lambda f, i=i, runs=runs: suite.run(
            env, f, work / f"s{i}-runs" / f"r{next(runs)}", "reference"), i)  # fmt: skip
        out.append(body | {"cost_usd": round(gw.spent_usd - spent, 4)})
        typer.echo(f"sample {i}: generated {body['generated']}, kept {len(body['tests'])}, "
                   f"dropped {len(body['dropped'])}, rounds {body['repair_rounds']}, "
                   f"${out[-1]['cost_usd']:.3f}")  # fmt: skip
    art = artifacts.put("naive_tests", {"model": propose.MODEL, "source_ref": ref,
                                        "repairs": propose.REPAIRS, "samples": out,
                                        "folder": str(work.relative_to(db.data_dir()))},
                        stage="baseline", stage_version=VERSION,
                        labels=["recorded" if replay else "real", "unsigned"])  # fmt: skip
    typer.echo(art.id)


def _platform(fix: str) -> tuple[dict, int, int]:
    """Arm (c) from its artifacts: per-node verdicts per variant, kills, mutants (B4)."""
    rr = artifacts.get(RUN_RECORDS).body
    v: dict[str, dict] = {}
    for x in rr["verdicts"]:
        v.setdefault(x["variant"], {})[x["node"]] = {"PASS": "PASS", "CONSISTENT_FAIL": "FAIL"
                                                    }.get(x["verdict"], "MIXED")  # fmt: skip
    rt = artifacts.get(RETEST).body["retests"][0]
    assert rt["after"]["variant"] == fix, rt["after"]
    v[fix] = {x["node"]: x["after"] for x in rt["fail_to_pass"]}  # the fixed run's flips
    g = artifacts.get(GATE_REPORT).body["mutants"]
    return v, g["killed"], g["total"]


def _images_match() -> tuple[bool, str]:
    fp = artifacts.get(CHECK_RECORDS).body["variants"]["reference"]["fingerprint"]
    pub = json.loads((db.data_dir() / "published.json").read_text())["runner"]
    ok = fp["images"]["backend"] == pub["base_id"] and fp["runner"] == pub["digest"]
    return ok, f"backend {pub['base_id'][:19]}, runner {pub['digest'][:19]}"


def _gate_mutants() -> list[str]:
    with db.connect() as c:
        outs = [json.loads(r["outputs"]) for r in c.execute(
            "SELECT outputs FROM stage_executions WHERE outputs LIKE ?", (f"%{GATE_REPORT}%",))]  # fmt: skip
    return sorted(m["name"] for m in artifacts.get(outs[0]["kill_matrix"]).body["mutants"])


@app.command()
def compare(naive_id: Annotated[str, typer.Option("--naive")]):
    """The three-arm table over the same variants and mutants (B1-B8)."""
    env, cfg = _env(), sut.load()
    src, ref, fix = Path(env.code_scope()["src"]), cfg["ref"], f"fixed:{suite.FIX}"
    ok, images = _images_match()
    if not ok:
        raise typer.Exit(f"platform arm's images differ from the current ones ({images}): "
                         "re-run the platform's execute before comparing (design B4)")  # fmt: skip
    env.ensure("reference")
    work = _work("compare")
    muts = suite.generate_mutants(env, work / "mutants")
    if (names := sorted(m["name"] for m in muts["list"])) != _gate_mutants():
        raise typer.Exit(f"mutants differ from the gate's ({len(names)} generated)")

    pv, pk, pm = _platform(fix)
    rows = [decide.row("Platform", pv, fix, pk, pm, {"source": RUN_RECORDS})]
    typer.echo("SUT's own tests")
    m = suite.measure(env, suite.sut_tests(src, ref, work / "sut-tests"), work / "sut", muts,
                      typer.echo)  # fmt: skip
    rows.append(decide.row("SUT's own tests", decide.verdicts(m["variants"]), fix,
                           len({x for ms in m["killed"].values() for x in ms}), m["mutants"],
                           {"killed": m["killed"]}))  # fmt: skip
    nt = artifacts.get(naive_id)
    for s in nt.body["samples"]:
        typer.echo(f"naive sample {s['sample']}")
        folder = suite.sut_tests(src, ref, work / f"naive-s{s['sample']}", with_tests=False)
        propose.write(folder, s["imports"], {t["name"]: t["source"] for t in s["tests"]})
        m = suite.measure(env, folder, work / f"naive-{s['sample']}", muts, typer.echo)
        rows.append(decide.row(f"Naive LLM, sample {s['sample']}", decide.verdicts(m["variants"]),
                               fix, len({x for ms in m["killed"].values() for x in ms}),
                               m["mutants"], {"killed": m["killed"], "cost_usd": s["cost_usd"],
                                              "generated": s["generated"],
                                              "dropped": len(s["dropped"]),
                                              "repaired": len(s["repaired"])}))  # fmt: skip
    notes = [
        (
            f"Same variants ({', '.join(suite.variants())}) and the gate's {pm} mutants for every "
            f"arm; strength = kills / {pm}. Detection: PASS on reference and FAIL on bug:FLAG "
            f"(all K runs); real bug: FAIL on reference, PASS on {fix}. No triage."
        ),
        (
            f"Platform from artifacts: run_records {RUN_RECORDS[:19]}… (K=3), retest "
            f"{RETEST[:19]}…, gate_report {GATE_REPORT[:19]}…; images match the current ones "
            f"({images})."
        ),
        (
            f"SUT and naive arms: in-process, K={suite.K}; {fix} overlays the patched file; the "
            "mutant is off during fixture setup (mutant_phase)."
        ),
        (
            f"Naive: {nt.body['model']}, upstream source at {ref[:7]} without our patches, ≤"
            f"{nt.body['repairs']} repair rounds on reference, then failing tests dropped. "
            "Each sample is its own row."
        ),
        "Race flag (BUG_FLAKE_RACE) is outside the headline: a timing bug (execute X5 not met).",
        (
            "Bias: the requirements, the seeded bugs and the platform's tests share one author, which "
            "favours the platform on 'seeded, authored'. Blind bugs (M7) exist for that."
        ),
    ]
    md = "# Baseline: three-arm table\n\n" + decide.markdown(rows, notes)
    (work / "baseline.md").write_text(md)
    art = artifacts.put("baseline_report", {"rows": rows, "notes": notes, "markdown": md,
                                            "folder": str(work.relative_to(db.data_dir()))},
                        stage="baseline", stage_version=VERSION,
                        parents=[naive_id, RUN_RECORDS, RETEST, GATE_REPORT, CHECK_RECORDS],
                        labels=nt.labels)  # fmt: skip
    typer.echo(md)
    typer.echo(art.id)


if __name__ == "__main__":
    app()
