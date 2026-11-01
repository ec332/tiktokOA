"""Aggregator, Worklist, Scorecard, Traceability, Coverage, Uncovered, AIDecisions (design
D1–D7). Pure: report.json from artifact bodies only. Every number is num(n, d, ids)."""

P0_CLASSES = ("PRODUCT_DEFECT", "INTERMITTENT_PRODUCT")
ADMITTED = ("ADMITTED", "ADMITTED_REDUNDANT", "ADMITTED_UNMEASURED")
HEALTH = ("FLAKY_TEST", "INSUFFICIENT_EVIDENCE", "TEST_DEFECT", "ENV", "DATA")
CATEGORY = {"boundary": "boundary", "error_exception": "exception", "error_guessing": "exception"}


def num(n: int, d: int | None, ids) -> dict:
    """A number with its composition (R1)."""
    return {"n": n, "d": d, "ids": sorted(ids)}


def build(spec: dict, cases: dict, gate: dict, rr: dict, tri: dict, decisions: list[dict],
          labels: list[str]) -> dict:  # fmt: skip
    criteria = {c["id"]: c for c in spec["criteria"]}
    case_by_id = {c["id"]: c for c in cases["cases"]}
    verdicts = rr["verdicts"]
    ref = {v["case_id"]: v for v in verdicts if v["variant"] == "reference"}
    defects = tri["defects"]
    return {
        "header": _header(rr, tri, decisions, labels, cases),
        "worklist": _worklist(criteria, case_by_id, gate, tri, verdicts),
        "scorecard": _scorecard(criteria, case_by_id, gate, rr, tri),
        "traceability": _traceability(criteria, case_by_id, ref, gate),
        "coverage": _coverage(case_by_id, ref),
        "uncovered": _uncovered(spec, cases, gate),
        "ai_decisions": _ai(spec, cases, gate, tri, decisions),
        "evidence": tri.get("evidence", {}),
        "defects": defects,
    }


def _no_admitted(gate, case_by_id) -> tuple[list[str], set[str]]:
    """Criteria in this run's scope (a gate row or a signed case) with no admitted test."""
    in_scope = {r["ac"] for r in gate["rows"]} | {c["ac"] for c in case_by_id.values()}
    admitted = {r["ac"] for r in gate["rows"] if r["status"] in ADMITTED}
    return sorted(in_scope - admitted), in_scope


def _header(rr, tri, decisions, labels, cases):
    unattended = [d for d in decisions if d["by"] == "unattended"]
    return {"labels": labels, "run_records": tri.get("run_records"), "execution": rr.get("execution"),
            "variants": rr.get("variants", {}), "k": rr.get("k"),
            "sut": cases.get("sut"), "cases_signed": (cases.get("signoff") or {}).get("signed"),
            "unattended_decisions": num(len(unattended), len(decisions), [f"review:{d['review_item']}" for d in unattended])}  # fmt: skip


def _worklist(criteria, case_by_id, gate, tri, verdicts):
    p0 = []
    for d in tri["defects"]:
        if d["class"] not in P0_CLASSES:
            continue
        c = criteria.get(d["ac"], {})
        p0.append({"defect": d["id"], "kind": d["kind"], "flag": d.get("flag"), "ac": d["ac"],
                   "title": c.get("title"), "expected": c.get("resolution") or c.get("then"),
                   "observed": d.get("observed"), "cases": num(len(d["cases"]), None, d["cases"]),
                   "root_cause": d.get("root_cause"), "suspects": d["suspects"], "evidence": d["evidence"],
                   "decision": d.get("decision") or {"by": "rules", "decision": "seeded (ground truth known)"},
                   "retest": "not retested (M5)"})  # fmt: skip
    p0.sort(key=lambda x: (x["kind"] != "real", x["defect"]))
    rows = gate["rows"]
    p1 = [{"what": "weak test (cannot fail on any mutant)", "case": r["case_id"], "ac": r["ac"],
           "closes_it": f"a row of {r['ac']} whose request reaches the item rules (the anonymous row stops at auth)"}
          for r in rows if r["status"] == "WEAK"]  # fmt: skip
    bound = {r["unit"] for r in rows}
    unbound = sorted({c["id"] for c in case_by_id.values()} - bound - {r["case_id"] for r in rows})
    p1 += [{"what": "signed case not bound to a test", "case": cid, "ac": case_by_id[cid]["ac"],
            "closes_it": f"bind {cid} (needs the clock/decode patches)"} for cid in unbound]  # fmt: skip
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
        "mutation_strength": num(m.get("killed", 0), m.get("total", 0), []),
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
        "mutation_strength": num(m.get("killed", 0), m.get("total", 0), []),
        "mutation_strength_note": "regression adequacy; the mutation scope (crud/deps/security) sits "
        "mostly under the test data, so this measures authentication more than the item rules",
        "mutation_per_criterion": per_crit,
        "triage_correct_seeded": num(len(right), len(truth), right),
        "localisation": loc,
        "fail_before_pass_after": "not run (M5)",
        "criteria_without_admitted_test": num(len(no_admitted), len(in_scope), no_admitted),
    }


def _traceability(criteria, case_by_id, ref, gate):
    out = []
    for r in gate["rows"]:
        c = criteria.get(r["ac"], {})
        v = ref.get(r["case_id"], {})
        out.append({"req": [s.get("lines") or f"{s.get('file')}:{s.get('line')}" for s in c.get("sources", [])],
                    "ac": r["ac"], "case": r["case_id"], "assertions": v.get("assertions") or [f"{r['case_id']}/*"],
                    "gate": r["status"], "verdict": v.get("verdict"), "dist": v.get("dist"),
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


def _uncovered(spec, cases, gate):
    rows = {r["unit"] for r in gate["rows"]}
    return {
        "criteria_out_of_scope": cases.get("not_tested", []),
        "deferred": cases.get("deferred", []),
        "skipped_templates": cases.get("attention", []),
        "assumed": [c["id"] for c in spec["criteria"] if c.get("status") == "assumed"],
        "unbound_cases": sorted(c["id"] for c in cases["cases"] if c["id"] not in rows),
        "behaviour_with_no_requirement": spec.get("unmapped_routes", []),
        "seeded_race": "BUG_FLAKE_RACE: no signed case reads an item right after creating it, so the "
        "suite cannot observe the late commit",
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
