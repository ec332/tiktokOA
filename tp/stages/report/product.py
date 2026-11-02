"""The report's happy-path section (M8 report D12-D14): defects with location and suggested
fix, problematic tests, areas to improve, rules used. Every line is filled from a record and
names the evidence it came from; nothing here is model-written except the fix suggestion, which
is labelled as a suggestion."""

TEST_REASON = {
    "CANDIDATE_DEFECT": "Fails on your branch for a confirmed requirement: likely a product defect.",
    "SPEC_GAP": "Fails on your branch, but its requirement was only assumed: check that answer.",
    "FLAKY": "Gave different results across repeated runs: not trusted until it is stable.",
    "WEAK": "Passes, but no code change the gate made was caught by it: it may check too little.",
    "REJECTED_ORACLE": "Failed in its own setup or binding: the test, not the product, is wrong.",
    "ERROR": "Hit an environment or platform error on your branch: no verdict.",
}
FINDING_REASON = {
    "PRODUCT_DEFECT": "Failed on every run with the product's responses: a product defect.",
    "INTERMITTENT_PRODUCT": "Fails only sometimes on the product's responses: intermittent defect.",
    "FLAKY_TEST": "Fails only sometimes, inside the test's own code: a flaky test.",
    "TEST_DEFECT": "Fails inside the test's own code: the test needs fixing.",
    "SPEC_GAP": "Fails, but its requirement was only assumed: check that answer.",
    "ENV": "The environment was unhealthy around this run: no verdict.",
    "DATA": "Test data setup failed: no verdict.",
    "INSUFFICIENT_EVIDENCE": "The rules could not decide: needs a look.",
}


def build(spec: dict, cases: dict, gate: dict, tri: dict, opts: dict) -> dict:
    crit = {c["id"]: c for c in spec["criteria"]}
    units = {u["id"]: u for u in cases.get("cases", [])}

    def test_of(unit: str | None, case_id: str | None, ac: str | None) -> dict:
        u = units.get(unit or "") or next((x for i, x in units.items()
                                           if case_id and case_id.startswith(i)), {})  # fmt: skip
        return {"case": case_id or unit, "title": u.get("purpose") or case_id or unit,
                "summary": u.get("defect_hypothesis", ""), "ac": ac,
                "criterion": crit.get(ac or "", {}).get("title", "")}  # fmt: skip

    defects = []
    for d in tri.get("defects", []):
        top = (d.get("suspects") or [{}])[0]
        defects.append({
            "id": d["id"], "kind": d["kind"], "class": d["class"], "ac": d["ac"],
            "criterion": crit.get(d["ac"], {}).get("title", ""),
            "observed": d.get("observed"), "failed_assertions": d.get("failed_assertions", []),
            "location": top and {k: top.get(k) for k in ("file", "line", "text", "basis")},
            "suspects": d.get("suspects", [])[:3], "evidence": d.get("evidence", []),
            "tests": [test_of(None, c, d["ac"]) for c in d.get("cases", [])],
            "suggestion": d.get("suggestion"),
        })  # fmt: skip

    # one cause, several requirements: same suspect line, same failing method and status
    groups: dict[tuple, list[dict]] = {}
    for d in defects:
        loc, o = d["location"] or {}, d["observed"] or {}
        key = (loc.get("file"), loc.get("line"), o.get("method"), o.get("status")) \
            if loc.get("file") else (d["id"],)  # fmt: skip
        groups.setdefault(key, []).append(d)
    causes = []
    for members in groups.values():
        lead = next((d for d in members if (d.get("suggestion") or {}).get("applies")), members[0])
        causes.append({**lead, "requirements": [{"id": d["id"], "ac": d["ac"],
                                                 "criterion": d["criterion"]} for d in members],
                       "tests": [t for d in members for t in d["tests"]],
                       "evidence": sorted({e for d in members for e in d["evidence"]})})  # fmt: skip

    problems, seen = [], set()
    for f in tri.get("findings", []):
        if f["variant"] != "reference":
            continue
        seen.add(f["node"])
        problems.append({**test_of(None, f["case_id"], f["ac"]), "status": f["class"],
                         "reason": FINDING_REASON.get(f["class"], f["rule"]),
                         "evidence": f["evidence"][:1], "source": "execute"})  # fmt: skip
    for r in gate.get("rows", []):
        if r["node"] in seen or r["status"] not in TEST_REASON:
            continue
        if r["status"] == "CANDIDATE_DEFECT":
            continue  # executed: its verdict is in the findings above
        problems.append({**test_of(r.get("unit"), r.get("case_id"), r.get("ac")),
                         "status": r["status"], "reason": TEST_REASON[r["status"]],
                         "evidence": [], "source": "gate"})  # fmt: skip

    merged: dict[tuple, dict] = {}  # boundary points (.a/.b) of one test: one row, counted
    for t in problems:
        key = (t["title"], t["ac"], t["status"])
        if key in merged:
            merged[key]["count"] += 1
            merged[key]["evidence"] += t["evidence"]
        else:
            merged[key] = {**t, "count": 1}
    problems = list(merged.values())

    improve = []
    for ac in gate.get("uncovered_criteria", []):
        improve.append({"kind": "uncovered", "title": f"No admitted test for {ac}",
                        "detail": crit.get(ac, {}).get("title", ""), "ac": ac})  # fmt: skip
    for s in gate.get("survivors", []):
        if s["class"] == "test_gap" and not s.get("equivalent"):
            improve.append({"kind": "survivor",
                            "title": f"A change at {s['file']}:{s['line']} went unnoticed",
                            "detail": s["diff"], "ac": ", ".join(s["criteria"])})  # fmt: skip
    for dec in spec.get("decisions", []):
        if dec.get("assumed"):
            improve.append({"kind": "assumed", "title": f"Answer assumed: {dec['about']}",
                            "detail": dec["answer"], "ac": None})  # fmt: skip
    for c in spec["criteria"]:
        if c.get("status") == "assumed" and c.get("review_needed"):
            improve.append({"kind": "spec_gap", "title": f"{c['id']} is still an assumption",
                            "detail": c.get("title", ""), "ac": c["id"]})  # fmt: skip

    signed = cases.get("signoff", {})
    return {
        "summary": {
            "tests": len(gate.get("rows", [])),
            "admitted": sum(1 for r in gate.get("rows", []) if r["status"].startswith("ADMITTED")),
            "defects": len(defects),
            "problematic": len(problems),
            "improve": len(improve),
            "approved": signed.get("approved"),
            "proposed": signed.get("lines"),
        },
        "defects": defects,
        "causes": causes,
        "problematic_tests": problems,
        "improve": improve,
        "rules": {
            "clarify": (opts.get("rules") or {}).get("clarify", []),
            "cases": (opts.get("rules") or {}).get("cases", []),
            "templates": opts.get("templates"),
            "gate": gate.get("settings"),
            "routes": opts.get("routes", []),
        },
    }
