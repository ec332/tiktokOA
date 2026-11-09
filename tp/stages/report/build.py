"""Aggregator, Worklist, Scorecard, Traceability, Coverage, Uncovered, AIDecisions (design
D1–D7). Pure given its inputs: artifact bodies, plus what `run_facts` reads from the run's sealed
folder and its target profile (cohesion/001 D8). Every number is num(n, d, ids)."""

import json

from tp.core import db, labels, profile

P0_CLASSES = ("PRODUCT_DEFECT", "INTERMITTENT_PRODUCT")
ADMITTED = ("ADMITTED", "ADMITTED_REDUNDANT", "ADMITTED_UNMEASURED")
HEALTH = ("FLAKY_TEST", "INSUFFICIENT_EVIDENCE", "TEST_DEFECT", "ENV", "DATA")
CATEGORY = {"boundary": "boundary", "error_exception": "exception", "error_guessing": "exception"}
# What each generic family claims (checks/001 D4); its criterion id is `G:<family>:<operation>`.
CLAIMS = {
    "properties": "no 5xx; status and body conform to the served spec (Schemathesis)",
    "auth_required": "no or invalid credentials: never 2xx on a secured operation, never 5xx",
    "unknown_id": "an ID that doesn't exist: never 2xx, never 5xx",
    "invalid_input": "a body its schema forbids: 4xx, never 5xx",
    "read_back": "declared fields round-trip from create to read",
    "delete_final": "a read after delete: never 2xx",
    "paging": "out-of-range paging or a bad token: never 5xx; a 2xx is a page",
    # on a local stand-in (simulated, C2), when discovery wired one (standins/001 D7)
    "dependency_called": "the trigger reaches the dependency's stand-in with what it was sent",
    "dependency_failure_handled": "with the dependency failing: in time, never a bare 5xx",
}
NOT_RAN = ("NOT_RUN", "SUSPECT_ENV")  # verdicts with no trusted execution behind them
ISOLATION = {0: "isolated", 1: "partly isolated (stand-ins)", 2: "live and local"}  # DR-3
SPEC_MISMATCH_SHOWN = 10  # groups listed before the rest are only counted (checks/001 D6)


def num(n: int, d: int | None, ids) -> dict:
    """A number with its composition (R1)."""
    return {"n": n, "d": d, "ids": sorted(ids)}


def _family(ac: str | None) -> str | None:
    return ac.split(":")[1] if ac and ac.startswith("G:") else None


def ceiling(strategies: dict, gate: dict) -> str | None:
    """The strongest claim the strategies that ran allow (checks/001 D12, external/001 E8)."""
    if not strategies:
        return None
    if (
        strategies.get("reach") == "external"
    ):  # no redeploy, no logs: what was seen (external/001 E8)
        k = (gate.get("settings") or {}).get("k", "K")
        return f"Seen on the live system, stable {k}/{k}"
    if strategies.get("mutation") == "in_process":
        return "Likely defect, catches planted bugs"
    props = any(_family(r.get("ac")) == "properties" and r["status"] not in ("EXCLUDED", "ERROR")
                for r in gate["rows"])  # fmt: skip
    return "Stable, property-checked" if props else "Stable"


def run_facts(rr: dict | None, t: str | None = None) -> dict:
    """What the artifacts don't hold (cohesion/001 D8): the target (C2 isolation, C5
    dependencies) from the profile the runner selected for this run, and the run's sealed
    records (C2 basis) and `dependencies.json` (standins/001 D9). Missing pieces stay empty."""
    try:
        p = profile.load(t=t)
        target = {"slug": p.slug, "reach": p.reach.strategy, "isolation": labels.isolation(p),
                  "dependencies": p.checks.value.get("dependencies") or [],
                  "strategies": {c: f.strategy for c, f in p.fields.items()}}  # fmt: skip
    except profile.ProfileError:
        target = None
    folder = db.data_dir() / rr["folder"] if (rr or {}).get("folder") else None
    res = folder / "results.jsonl" if folder else None
    dep = folder / "dependencies.json" if folder else None
    return {"target": target,
            "records": [json.loads(x) for x in res.read_text().splitlines() if x.strip()]
            if res and res.exists() else [],
            "seen": json.loads(dep.read_text()) if dep and dep.exists() else {}}  # fmt: skip


def basis(attempts: list[str], records: dict[str, dict]) -> str | None:
    """One result's basis (C2) over the records of its attempts; None when none is at hand."""
    got = {labels.basis(records[a]) for a in attempts if a in records}
    return next((b for b in ("simulated", "pre-generated", "real") if b in got), None)


def claim(target: dict | None, gate: dict | None, rr: dict | None, cases: dict | None,
          records: dict[str, dict], strategies: dict | None = None) -> dict:  # fmt: skip
    """The claim-first header (cohesion/001 D5): target · reach · isolation · ceiling · what ran
    (N of M; blocked ▸ why), and what the results rest on. One function for the report and the
    run page. `ran` is None until the gate has rows."""
    t = target or {}
    out = {"target": t.get("slug"), "reach": t.get("reach"), "isolation": t.get("isolation"),
           "isolation_words": ISOLATION.get(t.get("isolation")),
           "ceiling": ceiling(strategies or {}, gate or {"rows": []}),
           "ran": None, "blocked": [], "basis": {}}  # fmt: skip
    rows = (gate or {}).get("rows") or []
    if not rows:
        return out
    ref = {v["case_id"]: v for v in (rr or {}).get("verdicts", []) if v["variant"] == "reference"}
    why: dict[str, list[str]] = {}
    ran = []
    for r in rows:
        v = ref.get(r["case_id"])
        if r["status"] == "EXCLUDED":
            reason = f"excluded: {(r.get('excluded') or 'unknown').split(':')[0]}"
        elif r["status"] in ("REJECTED", "UNBOUND"):
            reason = "rejected at the gate" if r["status"] == "REJECTED" else "not bound"
        elif v is None or v["verdict"] in NOT_RAN:
            reason = f"not run: {v['verdict'] if v else 'no verdict'}"
        else:
            ran.append(r["case_id"])
            continue
        why.setdefault(reason, []).append(r["case_id"])
    bound = {r["unit"] for r in rows if r.get("unit")}
    if unbound := sorted(c["id"] for c in (cases or {}).get("cases", []) if c["id"] not in bound):
        why.setdefault("signed case not bound to a test", []).extend(unbound)
    total = len(rows) + len(unbound)
    by_basis: dict[str, list[str]] = {}
    for cid in ran:
        by_basis.setdefault(basis(ref[cid]["attempts"], records) or "not recorded", []).append(cid)
    return out | {"ran": num(len(ran), total, ran),
                  "blocked": [{"why": k, "cases": num(len(v), total, v)} for k, v in sorted(why.items())],
                  "basis": {k: num(len(v), len(ran), v) for k, v in sorted(by_basis.items())}}  # fmt: skip


def ledger(dependencies: list[dict], seen: dict[str, dict]) -> list[dict]:
    """The dependency ledger (c3, cohesion/001 D9): every outbound dependency discovery found,
    tested (simulated: the stand-in, each check's outcome and what it received) or not tested,
    with the reason."""
    out = []
    for d in dependencies:
        mine = {k: v for k, v in seen.items() if d.get("trigger") and k.endswith(f":{d['trigger']}")
                and k.startswith("G:dependency_")}  # fmt: skip
        entry = {k: d.get(k) for k in ("name", "kind", "via", "key", "trigger")}
        if d.get("status") == "wired" and mine:
            called = next((v for k, v in mine.items() if k.startswith("G:dependency_called:")), {})
            out.append(entry | {"status": "tested", "basis": "simulated",
                                "standin": f"tp-standin-{d['name']}",
                                "checks": [{"case": k, "outcome": v["outcome"], "fault": v.get("fault")}
                                           for k, v in sorted(mine.items())],
                                "received": called.get("received", [])})  # fmt: skip
        else:
            reason = d.get("reason") if d.get("status") != "wired" else None
            out.append(entry | {"status": "not tested",
                                "reason": reason or "wired, but no dependency check ran on it in this run"})  # fmt: skip
    return out


def build(spec: dict, cases: dict, gate: dict, rr: dict, tri: dict, decisions: list[dict],
          labels: list[str], bound_tests: dict | None = None,
          strategies: dict | None = None, timing: dict | None = None,
          facts: dict | None = None) -> dict:  # fmt: skip
    criteria = {c["id"]: c for c in spec["criteria"]}
    case_by_id = {c["id"]: c for c in cases["cases"]}
    verdicts = rr["verdicts"]
    ref = {v["case_id"]: v for v in verdicts if v["variant"] == "reference"}
    defects = tri["defects"]
    facts = run_facts(rr) if facts is None else facts
    records = {r["id"]: r for r in facts["records"]}
    deps = (facts["target"] or {}).get("dependencies") or []
    return {
        "header": _header(rr, tri, decisions, labels, cases)
        | {
            "strategies": strategies or {},
            "ceiling": ceiling(strategies or {}, gate),
            "claim": claim(facts["target"], gate, rr, cases, records, strategies),
        },
        "worklist": _worklist(criteria, case_by_id, gate, tri, verdicts, bound_tests, records),
        "checks": _checks(gate, tri),
        "dependencies": ledger(deps, facts["seen"]),
        "scorecard": _scorecard(criteria, case_by_id, gate, rr, tri),
        "traceability": _traceability(criteria, case_by_id, ref, gate, records),
        "coverage": _coverage(case_by_id, ref),
        "uncovered": _uncovered(spec, cases, gate, timing or {}),
        "ai_decisions": _ai(spec, cases, gate, tri, decisions),
        "evidence": tri.get("evidence", {}),
        "defects": defects,
    }


def _no_admitted(gate, case_by_id) -> tuple[list[str], set[str]]:
    """Criteria in this run's scope (a gate row or a signed case) with no admitted test."""
    in_scope = {r["ac"] for r in gate["rows"] if not _family(r.get("ac"))} | {
        c["ac"] for c in case_by_id.values()
    }  # generic families have no drafted criterion to admit a test for (checks/001)
    admitted = {r["ac"] for r in gate["rows"] if r["status"] in ADMITTED}
    return sorted(in_scope - admitted), in_scope


def _header(rr, tri, decisions, labels, cases):
    unattended = [d for d in decisions if d["by"] == "unattended"]
    network = next((n for v in rr.get("variants", {}).values()
                    if (n := (v.get("fingerprint") or {}).get("network"))), None)  # fmt: skip
    return ({"network": network} if network else {}) | {
            "labels": labels, "run_records": tri.get("run_records"), "execution": rr.get("execution"),
            "variants": rr.get("variants", {}), "k": rr.get("k"),
            "sut": cases.get("sut"), "cases_signed": (cases.get("signoff") or {}).get("signed"),
            "unattended_decisions": num(len(unattended), len(decisions), [f"review:{d['review_item']}" for d in unattended])}  # fmt: skip


def _checks(gate, tri):
    """Generic families: cases and gate outcomes per family, exclusions by reason, and server/spec
    disagreements grouped by criterion (checks/001 D6, D9)."""
    rows = [r for r in gate["rows"] if _family(r.get("ac"))]
    families = {}
    for fam, claim in CLAIMS.items():
        mine = [r for r in rows if _family(r["ac"]) == fam]
        if mine:
            statuses = sorted({r["status"] for r in mine})
            families[fam] = {"claim": claim, "cases": num(len(mine), None, [r["case_id"] for r in mine]),
                             "outcomes": {s: num(sum(r["status"] == s for r in mine), len(mine),
                                                 [r["case_id"] for r in mine if r["status"] == s]) for s in statuses}}  # fmt: skip
    reasons: dict[str, list] = {}
    for r in gate["rows"]:
        if r["status"] == "EXCLUDED":
            reasons.setdefault((r.get("excluded") or "unknown").split(":")[0], []).append(
                r["case_id"]
            )
    groups: dict[str, list] = {}
    for f in tri["findings"]:
        if f["class"] == "SPEC_MISMATCH" and f["variant"] == "reference":
            groups.setdefault(f["ac"], []).append(f)
    shown = sorted(groups)[:SPEC_MISMATCH_SHOWN]
    rest = sorted(groups)[SPEC_MISMATCH_SHOWN:]
    return {
        "families": families,
        "excluded": {k: num(len(v), len(gate["rows"]), v) for k, v in sorted(reasons.items())},
        "spec_mismatch": [
            {
                "group": ac,
                "claim": CLAIMS.get(_family(ac) or "", "a drafted criterion"),
                "findings": num(len(groups[ac]), None, [f["id"] for f in groups[ac]]),
                "rule": groups[ac][0]["rule"],
                "observed": groups[ac][0].get("observed"),
            }
            for ac in shown
        ],
        "spec_mismatch_more": num(len(rest), len(groups), rest),
    }


def _worklist(criteria, case_by_id, gate, tri, verdicts, bound_tests=None, records=None):
    p0 = []
    for d in tri["defects"]:
        if d["class"] not in P0_CLASSES:
            continue
        c = criteria.get(d["ac"], {}) or {"then": CLAIMS.get(_family(d["ac"]) or "")}
        p0.append({"defect": d["id"], "kind": d["kind"], "flag": d.get("flag"), "ac": d["ac"],
                   "title": c.get("title") or (d["ac"] if _family(d["ac"]) else None),
                   "expected": c.get("resolution") or c.get("then"),
                   "observed": d.get("observed"), "cases": num(len(d["cases"]), None, d["cases"]),
                   "root_cause": d.get("root_cause"), "suspects": d["suspects"], "evidence": d["evidence"],
                   "decision": d.get("decision") or {"by": "rules", "decision": "seeded (ground truth known)"},
                   "basis": basis([x for x in d["evidence"] if "#" not in x], records or {}),
                   "retest": "not retested (M5)"})  # fmt: skip
    p0.sort(key=lambda x: (x["kind"] != "real", x["defect"]))
    rows = gate["rows"]
    p1 = [{"what": "weak test (cannot fail on any mutant)", "case": r["case_id"], "ac": r["ac"],
           "closes_it": f"a row of {r['ac']} whose request reaches the item rules (the anonymous row stops at auth)"}
          for r in rows if r["status"] == "WEAK"]  # fmt: skip
    bound = {r["unit"] for r in rows}
    unbound = sorted({c["id"] for c in case_by_id.values()} - bound - {r["case_id"] for r in rows})
    # checks/001 D13: the recorded reason, never the template's "needs the clock/decode patches"
    why = {t["unit"]: t.get("reason") for t in (bound_tests or {}).get("tests", [])}
    p1 += [{"what": "signed case not bound to a test", "case": cid, "ac": case_by_id[cid]["ac"],
            "closes_it": f"bind {cid}" + (f": {why[cid]}" if why.get(cid) else "")}
           for cid in unbound]  # fmt: skip
    p1 += [{"what": "criterion with no admitted test", "ac": ac, "case": None,
            "closes_it": ", ".join(sorted(c["id"] for c in case_by_id.values() if c["ac"] == ac)) or "no case drafted"}
           for ac in _no_admitted(gate, case_by_id)[0]]  # fmt: skip
    health = [f for f in tri["findings"] if f["class"] in HEALTH]
    m = gate.get("mutants") or {}
    p2 = {
        "findings": [
            {
                "finding": f["id"],
                "class": f["class"],
                "rule": f["rule"],
                "dist": f["dist"],
                "evidence": f["evidence"][:1],
            }
            for f in health
        ],
        "quarantine": tri.get("quarantine", []),
        "redundant": num(
            sum(r["status"] == "ADMITTED_REDUNDANT" for r in rows),
            len(rows),
            [r["case_id"] for r in rows if r["status"] == "ADMITTED_REDUNDANT"],
        ),
        "mutation_strength": "not measured"
        if m.get("strength") == "not_measured"
        else num(m.get("killed", 0), m.get("total", 0), []),  # no mutation: black-box (D16)
    }
    gaps = [f for f in tri["findings"] if f["class"] == "SPEC_GAP"]
    p3 = [{"what": "spec gap", "ac": f["ac"], "case": f["case_id"], "why": f["rule"],
           "overridden": f.get("original_class"), "evidence": f["evidence"][:1]} for f in gaps]  # fmt: skip
    p3 += [{"what": "assumed criterion (never confirmed by a person)", "ac": c["id"], "case": None,
            "why": c["title"]} for c in criteria.values() if c.get("status") == "assumed"]  # fmt: skip
    return {"P0": p0, "P1": p1, "P2": p2, "P3": p3}


def _scorecard(criteria, case_by_id, gate, rr, tri):
    flags = sorted(v.removeprefix("bug:") for v in rr.get("variants", {}) if v.startswith("bug:"))
    seeded = {
        d["flag"]: d for d in tri["defects"] if d["kind"] == "seeded" and d["class"] in P0_CLASSES
    }
    per_flag = []
    for flag in flags:
        targeted = [
            r["case_id"] for r in gate["rows"] if (r.get("seeded") or {}).get("flag") == flag
        ]
        caught = sorted(c for d in tri["defects"] if d.get("flag") == flag for c in d["cases"])
        score = next((s for s in tri.get("scores", []) if s["flag"] == flag), None)
        per_flag.append({"flag": flag, "detected": flag in seeded, "targeted": num(len(set(caught) & set(targeted)), len(targeted), targeted),
                         "caught_by": caught, "rank": score and score["rank"]})  # fmt: skip
    real = [d for d in tri["defects"] if d["kind"] == "real"]
    confirmed = [d for d in real if d["class"] in P0_CLASSES]
    scores = tri.get("scores", [])
    loc = {f"top{k}": num(sum(s[f"top{k}"] for s in scores), len(scores), [s["defect"] for s in scores if s[f"top{k}"]])
           for k in (1, 3, 5)}  # fmt: skip
    # triage correctness on ground truth: targeted rows must be PRODUCT_DEFECT on their variant
    truth = [(r["case_id"], f"bug:{r['seeded']['flag']}") for r in gate["rows"]
             if (r.get("seeded") or {}).get("killed")]  # fmt: skip
    cls = {(f["case_id"], f["variant"]): f["class"] for f in tri["findings"]}
    right = [f"{c}@{v}" for c, v in truth if cls.get((c, v)) == "PRODUCT_DEFECT"]
    per_crit = {
        ac: num(c["killed"], c["reached_mutants"], [])
        for ac, c in (gate.get("criteria") or {}).items()
    }
    no_admitted, in_scope = _no_admitted(gate, case_by_id)
    m = gate.get("mutants") or {}
    return {
        "seeded_authored": num(
            sum(p["detected"] for p in per_flag),
            len(per_flag),
            [p["flag"] for p in per_flag if p["detected"]],
        ),
        "seeded_per_flag": per_flag,
        "seeded_blind": "not run (M7)",
        "real_defects": num(len(confirmed), len(real), [d["id"] for d in confirmed]),
        "mutation_strength": "not measured"
        if m.get("strength") == "not_measured"
        else num(m.get("killed", 0), m.get("total", 0), []),  # no mutation: black-box (D16)
        "mutation_strength_note": "regression adequacy; the mutation scope (crud/deps/security) sits "
        "mostly under the test data, so this measures authentication more than the item rules",
        "mutation_per_criterion": per_crit,
        "triage_correct_seeded": num(len(right), len(truth), right),
        "localisation": loc,
        "fail_before_pass_after": "not run (M5)",
        "criteria_without_admitted_test": num(len(no_admitted), len(in_scope), no_admitted),
    }


def _traceability(criteria, case_by_id, ref, gate, records=None):
    out = []
    for r in gate["rows"]:
        c = criteria.get(r["ac"], {})
        v = ref.get(r["case_id"], {})
        out.append({"req": [s.get("lines") or f"{s.get('file')}:{s.get('line')}" for s in c.get("sources", [])],
                    "ac": r["ac"], "case": r["case_id"], "assertions": v.get("assertions") or [f"{r['case_id']}/*"],
                    "gate": r["status"], "verdict": v.get("verdict"), "dist": v.get("dist"),
                    "basis": basis(v.get("attempts") or [], records or {}),
                    "evidence": (v.get("attempts") or [None])[0]})  # fmt: skip
    return out


def _coverage(case_by_id, ref):
    """From confirmed executions only (a verdict on reference that isn't ENV/NOT_RUN)."""
    out: dict[str, list] = {"normal": [], "boundary": [], "exception": []}
    for cid, v in ref.items():
        if v["verdict"] in ("NOT_RUN", "SUSPECT_ENV"):
            continue
        unit = cid.split(".")[0] if cid not in case_by_id else cid
        tech = (case_by_id.get(cid) or case_by_id.get(unit) or {}).get("technique", "")
        out[CATEGORY.get(tech, "normal")].append(cid)
    return {"by": "case technique (boundary → boundary; error_exception/error_guessing → exception; rest → normal)",
            **{k: num(len(v), len(ref), v) for k, v in out.items()}}  # fmt: skip


def _uncovered(spec, cases, gate, timing):
    rows = {r["unit"] for r in gate["rows"]}
    return {
        "criteria_out_of_scope": cases.get("not_tested", []),
        "deferred": cases.get("deferred", []),
        "skipped_templates": cases.get("attention", []),
        "assumed": [c["id"] for c in spec["criteria"] if c.get("status") == "assumed"],
        "unbound_cases": sorted(c["id"] for c in cases["cases"] if c["id"] not in rows),
        "behaviour_with_no_requirement": spec.get("unmapped_routes", []),
        "seeded_race": "; ".join(f"{f}: {why}" for f, why in timing.items()),
    }


def _ai(spec, cases, gate, tri, decisions):
    drafted = [c["id"] for c in cases["cases"] if c.get("quality") == "llm-drafted"]
    answers = spec.get("decisions", [])
    props = gate.get("proposals_summary") or {
        "proposed": len(gate.get("proposals", [])),
        "equivalent": sum(p.get("equivalent", False) for p in gate.get("proposals", [])),
    }
    return {
        "spec_answers": num(len(answers), None, [a["key"] for a in answers]),
        "spec_assumed": num(
            sum(a.get("assumed", False) for a in answers),
            len(answers),
            [a["key"] for a in answers if a.get("assumed")],
        ),
        "cases_llm_drafted": num(len(drafted), len(cases["cases"]), drafted),
        "cases_signoff": {
            "signed": (cases.get("signoff") or {}).get("signed"),
            "approved": num(
                (cases.get("signoff") or {}).get("approved", 0),
                (cases.get("signoff") or {}).get("lines", 0),
                [],
            ),
        },
        "gate_equivalence": {
            "equivalent": num(props["equivalent"], props["proposed"], []),
            "resolution": "not confirmed by a person; counted as survivors",
        },
        "triage": "no AI in M4 (rules only; the AI judge is M7)",
        "overrides": tri.get("overrides", []),
        "review_decisions": [
            {
                "item": d["review_item"],
                "kind": d["kind"],
                "decision": d["decision"],
                "by": d["by"],
                "reason": d["reason"],
            }
            for d in decisions
        ],
    }


POSITIONS = {
    "rank",
    "k",
    "line",
    "upstream_line",
    "line_no",
    "status",
    "step",
    "execution",
    "item",
    "review_item",
    "shuffle_seed",
    "observed",  # a copied request/response (evidence): its numbers are data, not report counts
    "received",  # what a stand-in received (evidence, cohesion/001 D9)
    "fault",  # the injected fault: a status or a delay, not a count
    "isolation",  # a level (DR-3), not a count
}  # positions and ids, not counts


def walk(report, path=""):
    """Every bare int/float outside num() (the R1 check): counts must carry their composition."""
    if isinstance(report, dict):
        if set(report) == {"n", "d", "ids"}:
            return
        for k, v in report.items():
            if k not in POSITIONS:
                yield from walk(v, f"{path}.{k}")
    elif isinstance(report, list):
        for i, v in enumerate(report):
            yield from walk(v, f"{path}[{i}]")
    elif isinstance(report, (int, float)) and not isinstance(report, bool):
        yield path
