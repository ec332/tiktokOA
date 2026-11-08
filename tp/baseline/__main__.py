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
from tp.core import artifacts, contract, db, llm, profile, sut
from tp.stages.gate.decide import code_refs as decide_refs

VERSION = "1.1.0"  # 1.1: handler mutants, blind bugs, assertion kills (design 003)
# The platform arm's existing artifacts (B4; retest status 004 handover).
RUN_RECORDS = "sha256:dfb6c25a8cf6f37254e682f70dbae61826df818b3fca48c8acafd83c4176df01"  # x252
CHECK_RECORDS = "sha256:aefea7ec748c16b033f58e7079276fd14c7aa13ebfa6115a8ccb2040e8a29814"  # #469
RETEST = "sha256:91080c4e8a394049bc134b7a475de8992d27aa70d4fb0295825cffc1d50a7acf"  # O-6 fixed

app = typer.Typer(add_completion=False)


def _env():
    contract.load()
    return contract.SERVICES["env"]


def _work(kind: str) -> Path:
    path = db.data_dir() / "runs" / f"baseline-{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True)
    return path


def _source(src: Path, ref: str) -> str:
    """What the naive LLM sees (B5): upstream app code at the pinned ref, none of our patches
    (they contain the seeded flags), plus the test fixtures and helpers, not the tests. The
    migrations folder (the parent of the profile's `state.migrations`) is left out."""
    migrations = str(Path(profile.load().state.value["migrations"]).parent) + "/"
    files = subprocess.run(["git", "-C", str(src), "ls-tree", "-r", "--name-only", ref,
                            profile.source_dir(), suite.tests_dir()], check=True,
                           capture_output=True, text=True).stdout.split()  # fmt: skip
    keep = [f for f in files if f.endswith(".py") and not f.startswith(migrations)
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


def _platform(fix: str, gate: dict) -> tuple[dict, int, int]:
    """Arm (c) from its artifacts: per-node verdicts per variant, mutmut-pool kills and total
    (B4; the M7 gate report adds handler mutants, which are split out)."""
    rr = artifacts.get(RUN_RECORDS).body
    v: dict[str, dict] = {}
    for x in rr["verdicts"]:
        v.setdefault(x["variant"], {})[x["node"]] = {"PASS": "PASS", "CONSISTENT_FAIL": "FAIL"
                                                    }.get(x["verdict"], "MIXED")  # fmt: skip
    rt = artifacts.get(RETEST).body["retests"][0]
    assert rt["after"]["variant"] == fix, rt["after"]
    v[fix] = {x["node"]: x["after"] for x in rt["fail_to_pass"]}  # the fixed run's flips
    g, h = gate["mutants"], gate.get("handlers", {"killed": 0, "total": 0})
    return v, g["killed"] - h["killed"], g["total"] - h["total"]


def _images_match() -> tuple[bool, str]:
    fp = artifacts.get(CHECK_RECORDS).body["variants"]["reference"]["fingerprint"]
    pub = json.loads((db.data_dir() / "published.json").read_text())["runner"]
    ok = fp["images"]["backend"] == pub["base_id"] and fp["runner"] == pub["digest"]
    return ok, f"backend {pub['base_id'][:19]}, runner {pub['digest'][:19]}"


def _gate_mutants(gate_report: str) -> set[str]:
    with db.connect() as c:
        outs = [json.loads(r["outputs"]) for r in c.execute(
            "SELECT outputs FROM stage_executions WHERE outputs LIKE ?", (f"%{gate_report}%",))]  # fmt: skip
    return {m["name"] for m in artifacts.get(outs[0]["kill_matrix"]).body["mutants"]}


@app.command()
def compare(naive_id: Annotated[str, typer.Option("--naive")],
            gate_report: Annotated[str, typer.Option("--gate-report", help="M7 gate (K8)")],
            blind_id: Annotated[str, typer.Option("--blind", help="blind_reveal artifact")],
            samples: Annotated[int, typer.Option(help="naive samples to measure")] = 1):  # fmt: skip
    """The three-arm table over the same variants and mutants (B1-B13)."""
    env, cfg = _env(), sut.load()
    src, ref, fix = Path(env.code_scope()["src"]), cfg["ref"], f"fixed:{suite.FIX}"
    ok, images = _images_match()
    if not ok:
        raise typer.Exit(f"platform arm's images differ from the current ones ({images}): "
                         "re-run the platform's execute before comparing (design B4)")  # fmt: skip
    gate, reveal = artifacts.get(gate_report).body, artifacts.get(blind_id).body
    spec = artifacts.get(gate["cases"]).body["spec"]
    env.ensure("reference")
    work = _work("compare")
    muts = suite.generate_mutants(env, work / "mutants",
                                  decide_refs(artifacts.get(spec).body))  # fmt: skip
    names = {m["name"] for m in muts["list"]}
    blind = set(reveal["names"])
    mm = {n for n in names if "__mutmut_" in n}
    handler = names - mm - blind
    if mm | handler != _gate_mutants(gate_report) or not blind <= names:
        raise typer.Exit("mutants differ from the gate's (design B1)")

    def split(m) -> tuple[int, dict]:
        ks = {x for ms in m["killed"].values() for x in ms}
        return len(ks & mm), {"handler_kills": len(ks & handler), "handlers": len(handler),
                              "seeded_blind": f"{len(ks & blind)}/{len(blind)}",
                              "asserted": len(set(m["asserted"]) & (mm | handler)),
                              "killed": m["killed"]}  # fmt: skip

    race_flag = next(iter(profile.timing_flags()), None)
    pv, pk, pm = _platform(fix, gate)
    h = gate["handlers"]
    rows = [decide.row("Platform", pv, fix, pk, pm, {
        "source": RUN_RECORDS, "handler_kills": h["killed"], "handlers": h["total"],
        "seeded_blind": f"{reveal['detected']}/{reveal['count']}"}, race_flag)]  # fmt: skip
    typer.echo("SUT's own tests")
    m = suite.measure(env, suite.sut_tests(src, ref, work / "sut-tests"), work / "sut", muts,
                      typer.echo)  # fmt: skip
    k, extra = split(m)
    rows.append(decide.row("SUT's own tests", decide.verdicts(m["variants"]), fix, k, len(mm),
                           extra, race_flag))  # fmt: skip
    nt = artifacts.get(naive_id)
    # One sample by default: in M6 the three samples agreed on every bug column, and each arm
    # costs a full variant + mutant pass (user, 2026-11-02).
    for s in nt.body["samples"][:samples]:
        typer.echo(f"naive sample {s['sample']}")
        folder = suite.sut_tests(src, ref, work / f"naive-s{s['sample']}", with_tests=False)
        propose.write(folder, s["imports"], {t["name"]: t["source"] for t in s["tests"]})
        m = suite.measure(env, folder, work / f"naive-{s['sample']}", muts, typer.echo)
        k, extra = split(m)
        rows.append(decide.row(f"Naive LLM, sample {s['sample']}", decide.verdicts(m["variants"]),
                               fix, k, len(mm), extra | {
                                   "cost_usd": s["cost_usd"], "generated": s["generated"],
                                   "dropped": len(s["dropped"]),
                                   "repaired": len(s["repaired"])}, race_flag))  # fmt: skip
    notes = [
        (f"Same variants ({', '.join(suite.variants())}) and mutants for every arm: mutmut's "
         f"{len(mm)} (crud/deps/security; 'Mutation strength') and {len(handler)} route-handler "
         f"mutants (K8; 'Handler strength'). Detection: PASS on reference and FAIL on bug:FLAG "
         f"(all K runs); real bug: FAIL on reference, PASS on {fix}. No triage."),
        (f"Seeded, blind: {len(blind)} handler mutants sealed before the gate run (hash in "
         f"blind_seal), held out of every pool above, revealed in {blind_id[:19]}…. A sealed "
         "mutant may be equivalent: a miss for every arm alike."),
        ("Assertion kills: mutants (both pools) some test kills with an AssertionError. The other "
         "kills are crashes, mostly from inline data setup. Platform: '—', its kills are already "
         "oracle-classified by the gate (D10) and its data setup runs with the mutant off."),
        (f"Platform from artifacts: run_records {RUN_RECORDS[:19]}… (K=3), retest "
         f"{RETEST[:19]}…, gate_report {gate_report[:19]}…; images match the current ones "
         f"({images})."),
        (f"SUT and naive arms: in-process, K={suite.K}; {fix} overlays the patched file; the "
         "mutant is off during fixture setup (mutant_phase)."),
        (f"Naive: {nt.body['model']}, upstream source at {ref[:7]} without our patches, ≤"
         f"{nt.body['repairs']} repair rounds on reference, then failing tests dropped. "
         "Each sample is its own row."),
        f"Race flag ({race_flag}) is outside the headline: a timing bug (execute X5).",
        ("Bias: the requirements, the seeded bugs and the platform's tests share one author, which "
         "favours the platform on 'seeded, authored'; 'seeded, blind' has no author."),
    ]  # fmt: skip
    md = "# Baseline: three-arm table\n\n" + decide.markdown(rows, notes)
    (work / "baseline.md").write_text(md)
    art = artifacts.put("baseline_report", {"rows": rows, "notes": notes, "markdown": md,
                                            "folder": str(work.relative_to(db.data_dir()))},
                        stage="baseline", stage_version=VERSION,
                        parents=[naive_id, RUN_RECORDS, RETEST, gate_report, blind_id,
                                 CHECK_RECORDS], labels=nt.labels)  # fmt: skip
    typer.echo(md)
    typer.echo(art.id)


if __name__ == "__main__":
    app()
