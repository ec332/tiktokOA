"""spec -> spec.md (deterministic, so the rendered file hashes the same on replay)."""


def _src(s: dict) -> str:
    return s["lines"] if s["kind"] == "requirement" else f"{s['file']}:{s['line']}"


def render(spec: dict) -> str:
    out = [
        f"# Spec v{spec['version']} — {spec['sut']['name']} @ {spec['sut']['ref'][:7]}",
        "",
        (
            f"Requirements: {spec['kind']} `{spec['requirements'][:19]}`. "
            f"Code index: {spec['index']['routes']} routes in {spec['index']['files']} files."
        ),
        "",
        "## Criteria",
        "",
        "| id | status | title | given / when / then | expected | sources | flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in spec["criteria"]:
        expected = "human judgement" if c.get("human_judgement") else (c.get("status_code") or "")
        flags = [*c.get("ambiguity", [])]
        if c.get("suspected_defect"):
            flags.append("SUSPECTED DEFECT")
        if c.get("review_needed"):
            flags.append("review needed")
        gwt = f"{c['given']} / {c['when']} / {c['then']}".replace("|", "\\|")
        out.append(
            f"| {c['id']} | {c['status']} | {c['title']} | {gwt} | {expected} | "
            f"{', '.join(_src(s) for s in c['sources'])} | {', '.join(flags)} |"
        )
    out += ["", "## Findings", ""]
    for f in spec["findings"]:
        ev = ", ".join(f"{e['file']}:{e['line']}" for e in f["evidence"])
        out.append(f"- **{f['kind']}**: {f['summary']} ({ev})")
    out += ["", "## Behaviour with no requirement (routes no criterion covers)", ""]
    out += [f"- {r}" for r in spec["unmapped_routes"]] or ["- none"]
    if spec["decisions"]:
        out += ["", "## Decisions", ""]
        for d in spec["decisions"]:
            answer = d["answer"] + (" (reused from an earlier run)" if d.get("reused") else "")
            out.append(f"- {d['about']}: {answer}{' (assumed)' if d.get('assumed') else ''}")
    return "\n".join(out) + "\n"
