"""The three-arm table: arm-neutral detection rules (design B2, B8). No LLM, no Docker.

Every arm is reduced to per-node verdicts per variant: PASS (passed in every run), FAIL
(failed in every run) or anything else (intermittent, skipped), plus its mutant kill count."""

RACE = "BUG_FLAKE_RACE"  # timing bug; X5 isn't met, so it is reported apart from the headline


def verdict(runs: list[dict[str, str]], node: str) -> str:
    seen = {r.get(node) for r in runs}
    return "PASS" if seen == {"passed"} else "FAIL" if seen == {"failed"} else "MIXED"


def verdicts(variant_runs: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    """{variant: [{node: outcome}] * K} -> {variant: {node: PASS|FAIL|MIXED}}."""
    return {v: {n: verdict(runs, n) for n in sorted({n for r in runs for n in r})}
            for v, runs in variant_runs.items()}  # fmt: skip


def validates(v: dict[str, dict[str, str]], fix: str) -> dict[str, list[str]]:
    """node -> the bugs it validates. Seeded: PASS on reference, FAIL on bug:FLAG. Real: FAIL on
    reference, PASS on the fixed variant (the retest fail->pass rule)."""
    ref, out = v["reference"], {}
    for node, r in ref.items():
        bugs = [var[4:] for var, res in v.items()
                if var.startswith("bug:") and r == "PASS" and res.get(node) == "FAIL"]  # fmt: skip
        if r == "FAIL" and v.get(fix, {}).get(node) == "PASS":
            bugs.append("real")
        if bugs:
            out[node] = bugs
    return out


def race(v: dict[str, dict[str, str]]) -> str:
    """CONSISTENT / INTERMITTENT / no: the best any reference-passing node does on the race flag."""
    got = {res for n, res in v.get(f"bug:{RACE}", {}).items() if v["reference"].get(n) == "PASS"}
    return "CONSISTENT" if "FAIL" in got else "INTERMITTENT" if "MIXED" in got else "no"


def row(arm: str, v: dict, fix: str, kills: int, mutants: int, extra: dict | None = None) -> dict:
    flags = sorted(var[4:] for var in v if var.startswith("bug:") and var[4:] != RACE)
    val = validates(v, fix)
    found = sorted({b for bugs in val.values() for b in bugs if b in flags})
    bug_nodes = sum(1 for b in val.values() if set(b) - {RACE})
    tests = len(v["reference"])
    return {"arm": arm, "tests": tests,
            "seeded_authored": f"{len(found)}/{len(flags)}", "seeded_found": found,
            "seeded_blind": "— (M7)", "race": race(v),
            "real_bug": any("real" in b for b in val.values()),
            "kills": kills, "mutants": mutants, "strength": round(kills / mutants, 3),
            "bug_validating": bug_nodes,
            "bug_validating_share": round(bug_nodes / tests, 3) if tests else 0.0,
            "validating_nodes": val, **(extra or {})}  # fmt: skip


def markdown(rows: list[dict], notes: list[str]) -> str:
    cols = [("Arm", "arm"), ("Tests", "tests"), ("Seeded, authored", "seeded_authored"),
            ("Seeded, blind", "seeded_blind"), ("Race flag", "race"), ("Real bug O-6", "real"),
            ("Mutation strength", "strength_txt"), ("Bug-validating share", "share_txt"),
            ("LLM cost", "cost")]  # fmt: skip
    lines = ["| " + " | ".join(c for c, _ in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        cell = r | {"real": "yes" if r["real_bug"] else "no",
                    "seeded_authored": f"{r['seeded_authored']} ({', '.join(r['seeded_found']) or '—'})",
                    "strength_txt": f"{r['strength']:.3f} ({r['kills']}/{r['mutants']})",
                    "share_txt": f"{r['bug_validating_share']:.3f} ({r['bug_validating']}/{r['tests']})",
                    "cost": f"${r['cost_usd']:.3f}" if "cost_usd" in r else "—"}  # fmt: skip
        lines.append("| " + " | ".join(str(cell[k]) for _, k in cols) + " |")
    return "\n".join(lines + [""] + [f"- {n}" for n in notes]) + "\n"
