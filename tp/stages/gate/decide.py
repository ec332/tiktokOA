"""The gate's deterministic decisions (plan Stage 4; gate design D10, D12, D14). Pure functions
over result records (platform_rt JSONL) so every rule has a fast test."""

import csv
import io
from collections import Counter

PRODUCT = {"ASSERTION", "CONTRACT", "PRODUCT_ERROR"}
BINDING = {"BINDING", "CONFORMANCE", "DATA"}
KILL = PRODUCT | {"CONFORMANCE", "TIMEOUT"}  # failing for the reason the test claims (D10)
NEAR = 3  # † lines: a survivor this close to a criterion's code ref is a test gap (D12)
# Per-run settings (M8 gate D22): defaults = the values above; a run may change them.
DEFAULTS = {"k": 5, "min_kills": 1, "near": NEAR, "every_criterion": True}
LIMITS = {"k": (2, 10), "min_kills": (1, 5), "near": (0, 20)}


def settings(opts: dict) -> dict:
    """The run's gate settings, clamped to LIMITS; unknown keys are ignored."""
    given = (opts or {}).get("gate") or {}
    out = dict(DEFAULTS)
    for k, (lo, hi) in LIMITS.items():
        if isinstance(given.get(k), int) and not isinstance(given.get(k), bool):
            out[k] = min(hi, max(lo, given[k]))
    if isinstance(given.get("every_criterion"), bool):
        out["every_criterion"] = given["every_criterion"]
    return out


def stability(runs: list[list[dict]]) -> dict[str, dict]:
    """K runs (+ the shuffled one) -> per node: {outcome, origin, stable, outcomes}."""
    seen: dict[str, list[dict]] = {}
    for run in runs:
        for r in run:
            seen.setdefault(r["nodeid"], []).append(r)
    out = {}
    for node, recs in seen.items():
        outcomes = [r["outcome"] for r in recs]
        origins = [r["origin"] for r in recs if r["origin"]]
        stable = len(set(outcomes)) == 1 and len(recs) == len(runs) and len(set(origins)) <= 1
        out[node] = {"outcome": outcomes[0], "origin": origins[0] if origins else None,
                     "stable": stable, "outcomes": dict(Counter(outcomes)),
                     "case_id": recs[0]["case_id"]}  # fmt: skip
    return out


def route(node: dict, criterion: dict, signed: bool) -> str | None:
    """ReferenceRouter: None for a stable pass (goes on to admission), else a status."""
    if not node["stable"]:
        return "FLAKY"
    if node["outcome"] == "passed":
        return None
    origin = node["origin"]
    if origin in BINDING:
        return "REJECTED_ORACLE"
    if origin not in PRODUCT:
        return "ERROR"  # ENV / PLATFORM / TIMEOUT on reference: not the test's verdict
    if (criterion.get("status") == "confirmed" and signed
            and not criterion.get("human_judgement")):  # fmt: skip
        return "CANDIDATE_DEFECT"
    return "SPEC_GAP"  # assumed / observed / human judgement: the spec, not the product, is open


def kills(matrix: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    """{mutant: records} -> {node: {mutant: "K" kill | "S" setup failure}} (D10)."""
    cells: dict[str, dict[str, str]] = {}
    for mutant, recs in matrix.items():
        for r in recs:
            if r["outcome"] != "failed":
                continue
            cell = "K" if r["origin"] in KILL else "S" if r["origin"] in BINDING else None
            if cell:
                cells.setdefault(r["nodeid"], {})[mutant] = cell
    return cells


def admit(node: str, unit_of: dict[str, str], cells: dict, seeded_ok: bool,
          measurable: bool, min_kills: int = 1) -> tuple[str, int, int]:  # fmt: skip
    """-> (status, kills, unique kills). Unique = no other *unit* kills that mutant. A test
    with fewer than `min_kills` kills (and no seeded kill) is WEAK when kills can be measured."""
    mine = {m for m, c in cells.get(node, {}).items() if c == "K"}
    if measurable and not seeded_ok and 0 < len(mine) < min_kills:
        return "WEAK", len(mine), len(mine - {m for n, row in cells.items()
                                              if unit_of.get(n) != unit_of.get(node)
                                              for m, c in row.items() if c == "K"})  # fmt: skip
    others = {m for n, row in cells.items() if unit_of.get(n) != unit_of.get(node)
              for m, c in row.items() if c == "K"}  # fmt: skip
    unique = mine - others
    if unique or seeded_ok:
        status = "ADMITTED"
    elif mine:
        status = "ADMITTED_REDUNDANT"
    elif not measurable:
        status = "ADMITTED_UNMEASURED"
    else:
        status = "WEAK"
    return status, len(mine), len(unique)


def code_refs(spec: dict) -> list[tuple[str, int, str]]:
    return [(s["file"], int(s["line"]), c["id"]) for c in spec["criteria"]
            for s in c.get("sources", []) if s.get("kind") == "code" and s.get("line")]  # fmt: skip


def survivors(mutants: list[dict], cells: dict, linked: dict[str, list[str]],
              refs: list[tuple[str, int, str]], within: int = NEAR) -> list[dict]:  # fmt: skip
    killed = {m for row in cells.values() for m, c in row.items() if c == "K"}
    out = []
    for m in mutants:
        if m["name"] in killed:
            continue
        near = sorted({cid for f, ln, cid in refs
                       if f == m["file"] and m["line"] and abs(ln - m["line"]) <= within})  # fmt: skip
        kind = "unclassified" if not m["line"] else "test_gap" if near else "out_of_scope"
        out.append({"mutant": m["name"], "file": m["file"], "line": m["line"], "class": kind,
                    "criteria": near, "covered": bool(linked.get(m["name"])),
                    "diff": m["diff"]})  # fmt: skip
    return out


def strength(killed: int, total: int, equivalent: int) -> float | None:
    """killed ÷ (total − human-confirmed equivalent) (plan K6)."""
    denom = total - equivalent
    return round(killed / denom, 3) if denom > 0 else None


def matrix_csv(rows: list[str], mutants: list[str], cells: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["test", *mutants])
    for node in rows:
        w.writerow([node, *(cells.get(node, {}).get(m, ".") for m in mutants)])
    return buf.getvalue()


def uncovered(criteria_in_scope: list[str], rows: list[dict]) -> list[str]:
    """Criteria with cases but no admitted (or defect-finding) test (M8 gate D22)."""
    ok = {
        r["ac"]
        for r in rows
        if r["status"].startswith("ADMITTED") or r["status"] == "CANDIDATE_DEFECT"
    }  # a test that found a defect covers it
    return sorted(c for c in criteria_in_scope if c not in ok)
