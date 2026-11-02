"""IterationFeeder (plan V4; retest design 005 D11-D13): the worklist -> case candidates for a
second cycle against the same frozen spec. Deterministic.

  tp retest feed --gate-report <id> --report <id>     -> `iteration` artifact
  tp stage run cases --input spec=<id> --opt iteration=<id>   (cases design 006)"""

from typing import Annotated

import typer

from tp.core import artifacts

CAP = 10  # candidates (criteria): keeps the second sign-off small
NOT_A_CASE_GAP = "signed case not bound to a test"  # a binding gap: new cases can't close it
app = typer.Typer(no_args_is_help=True, help="Retest tools")


def candidates(gate: dict, report: dict, cases: dict) -> list[dict]:
    """Per criterion, the reasons it needs more cases; criteria with no admitted test first,
    then test-gap survivors (most first), then deferred cases. At most CAP criteria."""
    basis: dict[str, list[str]] = {}
    for it in report["worklist"].get("P1", []):
        if it["what"] != NOT_A_CASE_GAP:
            basis.setdefault(it["ac"], []).append(f"{it['what']}: {it['closes_it']}")
    first = list(basis)
    gaps: dict[str, list[str]] = {}
    for s in gate["survivors"]:
        if s["class"] == "test_gap" and not s.get("equivalent"):
            for ac in s["criteria"]:
                gaps.setdefault(ac, []).append(
                    f"surviving mutant at {s['file']}:{s['line']}: {s['diff'].replace(chr(10), ' ')}"
                )
    for ac in sorted(gaps, key=lambda a: -len(gaps[a])):
        basis.setdefault(ac, []).extend(gaps[ac])
    for d in cases.get("deferred", []):
        basis.setdefault(d["ac"], []).append(
            f"deferred case: {d.get('title') or d.get('id')} ({d.get('deferred')})"
        )
    order = first + [a for a in basis if a not in first]
    return [{"criterion": ac, "basis": basis[ac][:5]} for ac in order[:CAP]]


@app.command()
def feed(gate_report: Annotated[str, typer.Option("--gate-report")],
         report: Annotated[str, typer.Option("--report")]):  # fmt: skip
    gate = artifacts.get(gate_report).body
    cases_art = artifacts.get(gate["cases"])
    rep = artifacts.get(report).body["report"]
    out = candidates(gate, rep, cases_art.body)
    art = artifacts.put("iteration", {"spec": cases_art.body["spec"], "candidates": out,
                                      "gate_report": gate_report, "report": report},
                        stage="retest", parents=[gate_report, report, cases_art.id],
                        labels=["real"])  # fmt: skip
    for c in out:
        typer.echo(f"{c['criterion']}: {len(c['basis'])} reason(s), first: {c['basis'][0][:100]}")
    typer.echo(art.id)
