"""Renderer (design D8): one static report.html (cited evidence inline), summary.md and the CLI
worklist, all from report.json. stdlib only."""

import json
from html import escape as e


def _n(x) -> str:
    if isinstance(x, dict) and set(x) == {"n", "d", "ids"}:
        frac = f"{x['n']}/{x['d']}" if x["d"] is not None else str(x["n"])
        return frac + (f" ({100 * x['n'] / x['d']:.0f}%)" if x["d"] else "")
    return str(x)


def _num(x) -> str:
    """A number that expands to its composition."""
    if isinstance(x, dict) and set(x) == {"n", "d", "ids"} and x["ids"]:
        ids = ", ".join(_ref(i) for i in x["ids"])
        return (
            f"<details class=num><summary>{e(_n(x))}</summary><div class=ids>{ids}</div></details>"
        )
    return f"<span class=num>{e(_n(x))}</span>"


def _ref(eid: str) -> str:
    return (
        f'<a href="#{e(eid)}">{e(eid)}</a>'
        if eid.startswith(("ex:", "log:"))
        else f"<code>{e(eid)}</code>"
    )


def _suspects(ss: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{i}</td><td><code>{e(s['file'])}:{e(str(s['upstream_line'] or '–'))}</code>"
        f"<div class=muted>running: :{e(str(s['line'] or '–'))}</div></td>"
        f"<td><code>{e(s['text'] or '')}</code></td><td>{e(s['basis'])}</td></tr>"
        for i, s in enumerate(ss, 1))  # fmt: skip
    return ("<table><thead><tr><th>#</th><th>upstream file:line</th><th>code</th><th>basis</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")  # fmt: skip


def _evidence(eid: str, ev: dict) -> str:
    if ev["kind"] == "log":
        body = f"<pre>{e(ev['line'])}\n\n{e(ev['traceback'])}</pre>"
        title = f"backend log, {ev['variant']} line {ev['line_no']}"
    elif ev["kind"] == "exchange":
        q = "&".join(f"{k}={v}" for k, v in (ev.get("query") or {}).items())
        req = f"{ev['method']} {ev['path']}{'?' + q if q else ''}"
        if ev.get("json") is not None:
            req += "\n" + json.dumps(ev["json"])
        body = f"<pre>{e(req)}\n→ {ev['status']}\n{e(ev['body'])}\n\nX-Test-Trace: {e(ev.get('x_test_trace') or '')}</pre>"
        title = "HTTP exchange"
    else:
        body = f"<pre>{e(json.dumps(ev, indent=1))}</pre>"
        title = "run record"
    return f'<details class=ev id="{e(eid)}"><summary><code>{e(eid)}</code> {e(title)}</summary>{body}</details>'


def claim_line(c: dict) -> str:
    """The claim-first header as one line of text (cohesion/001 D5); the run page builds the same."""
    if c.get("ran") is None:
        ran = "not run yet"
    else:
        blocked = "; ".join(f"{b['why']} {_n(b['cases'])}" for b in c["blocked"])
        ran = f"ran {c['ran']['n']} of {c['ran']['d']}" + (
            f"; blocked ▸ {blocked}" if blocked else ""
        )
    level = (
        "" if c.get("isolation") is None else f"isolation {c['isolation']} ({c['isolation_words']})"
    )
    rests = ", ".join(f"{k} {_n(v)}" for k, v in c.get("basis", {}).items())
    parts = [
        c.get("target") or "no target",
        c.get("reach") or "",
        level,
        c.get("ceiling") or "",
        ran,
    ]
    return " · ".join(x for x in parts if x) + (f" · rests on: {rests}" if rests else "")


def _claim(h: dict) -> str:
    out = f"<p class=claim><b>{e(claim_line(h['claim']))}</b></p>" if h.get("claim") else ""
    if not h.get("ceiling"):
        return out
    ran = ", ".join(f"{k}={v}" for k, v in h["strategies"].items())
    return (
        out
        + f"<p>Strongest claim: <b>{e(h['ceiling'])}</b> <span class=muted>· strategies: {e(ran)}</span></p>"
    )


def _ledger_html(deps: list[dict]) -> str:
    """Every outbound dependency discovery found: tested (simulated) or not, with the reason (c3)."""
    if not deps:
        return ""
    rows = []
    for d in deps:
        if d["status"] == "tested":
            checks = " · ".join(f"<code>{e(c['case'])}</code> {e(c['outcome'])}"
                                + (f" (fault {e(c['fault']['mode'])} {e(str(c['fault']['value']))})" if c.get("fault") else "")
                                for c in d["checks"])  # fmt: skip
            what = (f"<span class=label>simulated</span> on <code>{e(d['standin'])}</code>: {checks}"
                    f"<details><summary>received ({len(d['received'])})</summary><pre>{e(json.dumps(d['received'], indent=1))}</pre></details>")  # fmt: skip
        else:
            what = f"not tested: {e(d['reason'])}"
        rows.append(
            f"<tr><td>{e(d['name'])}</td><td>{e(d['kind'])}</td><td>{e(str(d.get('via')))} <code>{e(str(d.get('key')))}</code></td><td>{what}</td></tr>"
        )
    return ("<section><h2>Outbound dependencies</h2><p class=muted>Every dependency discovery found. A tested one "
            "ran against a local stand-in, so its result is simulated, never a real third party.</p><table><thead>"
            "<tr><th>dependency</th><th>kind</th><th>found by</th><th>result</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></section>")  # fmt: skip


def _checks_html(c: dict) -> str:
    """Generic families and server/spec disagreements (checks/001 D6). Empty when none ran."""
    if not c["families"] and not c["spec_mismatch"]:
        return ""
    out = [
        "<section><h2>Generic checks</h2><table><thead><tr><th>family</th><th>claim</th><th>cases</th><th>gate outcomes</th></tr></thead><tbody>"
    ]
    out += [f"<tr><td>{e(f)}</td><td>{e(v['claim'])}</td><td>{_num(v['cases'])}</td><td>"
            + " · ".join(f"{e(s)} {_num(n)}" for s, n in v["outcomes"].items()) + "</td></tr>"
            for f, v in c["families"].items()]  # fmt: skip
    out.append("</tbody></table>")
    if c["excluded"]:
        out.append(
            "<p>Excluded: "
            + " · ".join(f"{e(k)} {_num(v)}" for k, v in c["excluded"].items())
            + "</p>"
        )
    out.append("</section>")
    n = len(c["spec_mismatch"]) + c["spec_mismatch_more"]["n"]
    out.append(f"<section><h2>Server and spec disagree ({n} operations)</h2><p class=muted>Valid HTTP that "
               "contradicts the served spec. Either side may be wrong: these become issues, never "
               "defects or PRs, unless Clarify settles which side is intended.</p>")  # fmt: skip
    for g in c["spec_mismatch"]:
        o = g["observed"] or {}
        out.append(f"<article class=card><h3><code>{e(g['group'])}</code></h3><p>{e(g['rule'])} · "
                   f"findings {_num(g['findings'])}</p><p><b>Observed</b> <code>{e(str(o.get('request')))} → "
                   f"{e(str(o.get('status')))}</code>{(' · <b>checks</b> ' + e(', '.join(o['checks']))) if o.get('checks') else ''}</p>"
                   f"{('<pre>' + e(o['curl']) + '</pre>') if o.get('curl') else ''}"
                   f"<p class=muted>{e(str(o.get('error') or ''))}</p></article>")  # fmt: skip
    if c["spec_mismatch_more"]["n"]:
        out.append(f"<p>And {_num(c['spec_mismatch_more'])} more, not listed.</p>")
    return "".join(out) + "</section>"


def html(r: dict) -> str:
    h, w, sc = r["header"], r["worklist"], r["scorecard"]
    labels = " ".join(f"<span class=label>{e(x)}</span>" for x in h["labels"])
    unatt = h["unattended_decisions"]
    variants = ", ".join(f"<code>{e(v)}</code>" for v in h["variants"])
    parts = [
        f"""<header><h1>Test report</h1><div>{labels}
<span class=label>no AI in run/judge/report</span>{'<span class="label warn">unattended decisions ' + e(_n(unatt)) + "</span>" if unatt["n"] else ""}</div>
<p class=muted>SUT <code>{e(str((h.get("sut") or {}).get("ref", "")))[:12]}</code> · variants {variants} · K={h["k"]} · run_records <code>{e(str(h["run_records"])[:19])}</code></p>{f"<p class=muted>Network: at run time {e(h['network']['run'])}; build: {e(h['network']['build'])}.</p>" if h.get("network") else ""}{_claim(h)}</header>"""
    ]

    parts.append("<section><h2>P0 — defects</h2>")
    if not w["P0"]:
        parts.append("<p>None.</p>")
    for d in w["P0"]:
        dec = d["decision"]
        kind = "real defect" if d["kind"] == "real" else f"seeded ({d['flag']})"
        obs = d.get("observed") or {}
        q = "&".join(f"{k}={v}" for k, v in (obs.get("query") or {}).items())
        rc = d.get("root_cause") or {}
        parts.append(f"""<article class=card><h3>{e(d["ac"])} · {e(d["title"] or "")} <span class=label>{e(kind)}</span> <span class=label>basis: {e(str(d.get("basis") or "not recorded"))}</span></h3>
<p><b>Expected</b> {e(str(d["expected"]))} · <b>observed</b> <code>{e(obs.get("method", ""))} {e(obs.get("path", ""))}{"?" + e(q) if q else ""} → {e(str(obs.get("status", "")))}</code>
{("· <b>root cause</b> <code>" + e(rc.get("cls", "")) + ": " + e(rc.get("msg", "")) + "</code>") if rc else ""}</p>
<p>cases {_num(d["cases"])} · decided by <b>{e(str(dec.get("by")))}</b> ({e(str(dec.get("decision")))}{(": " + e(dec["reason"])) if dec.get("reason") else ""}) · retest: {e(d["retest"])}</p>
<h4>Suspects</h4>{_suspects(d["suspects"])}
<h4>Evidence</h4>{"".join(_evidence(x, r["evidence"][x]) for x in d["evidence"] if x in r["evidence"])}</article>""")
    parts.append("</section>")

    parts.append(
        "<section><h2>P1 — untested or weak</h2><table><thead><tr><th>what</th><th>criterion</th><th>case</th><th>what would close it</th></tr></thead><tbody>"
    )
    parts += [
        f"<tr><td>{e(p['what'])}</td><td>{e(p['ac'])}</td><td>{e(p['case'] or '–')}</td><td>{e(p['closes_it'])}</td></tr>"
        for p in w["P1"]
    ]
    parts.append("</tbody></table></section>")

    p2 = w["P2"]
    parts.append(
        f"<section><h2>P2 — test health</h2><p>redundant {_num(p2['redundant'])} · mutation strength {_num(p2['mutation_strength'])} · quarantined {len(p2['quarantine'])}</p>"
    )
    if p2["findings"]:
        parts.append(
            "<table><thead><tr><th>finding</th><th>class</th><th>dist</th><th>rule</th></tr></thead><tbody>"
        )
        parts += [
            f"<tr><td><code>{e(f['finding'])}</code></td><td>{e(f['class'])}</td><td>{e(f['dist'])}</td><td>{e(f['rule'])}</td></tr>"
            for f in p2["findings"]
        ]
        parts.append("</tbody></table>")
    parts.append("</section>")

    parts.append(
        "<section><h2>P3 — spec and code drift</h2><table><thead><tr><th>what</th><th>criterion</th><th>case</th><th>why</th></tr></thead><tbody>"
    )
    parts += [
        f"<tr><td>{e(p['what'])}{' (overridden from ' + e(p['overridden']) + ')' if p.get('overridden') else ''}</td><td>{e(p['ac'])}</td><td>{e(p['case'] or '–')}</td><td>{e(p['why'])}</td></tr>"
        for p in w["P3"]
    ]
    parts.append("</tbody></table></section>")

    parts.append(_checks_html(r["checks"]))
    parts.append(_ledger_html(r.get("dependencies", [])))

    rows = [
        ("Seeded detected (authored)", _num(sc["seeded_authored"])),
        ("Seeded detected (blind)", e(sc["seeded_blind"])),
        ("Real defects", _num(sc["real_defects"])),
        (
            "Mutation strength",
            _num(sc["mutation_strength"])
            + f"<div class=muted>{e(sc['mutation_strength_note'])}</div>",
        ),
        ("Triage correct on seeded rows", _num(sc["triage_correct_seeded"])),
    ]
    rows += [(f"Localisation {k}", _num(v)) for k, v in sc["localisation"].items()]
    rows += [
        ("Fail before / pass after", e(sc["fail_before_pass_after"])),
        ("Criteria without an admitted test", _num(sc["criteria_without_admitted_test"])),
    ]
    parts.append(
        "<section><h2>Scorecard</h2><table><tbody>"
        + "".join(f"<tr><th>{e(k)}</th><td>{v}</td></tr>" for k, v in rows)
        + "</tbody></table>"
    )
    parts.append(
        "<table><thead><tr><th>flag</th><th>detected</th><th>targeted rows caught</th><th>caught by</th><th>localisation rank</th></tr></thead><tbody>"
    )
    parts += [
        f"<tr><td><code>{e(p['flag'])}</code></td><td>{'yes' if p['detected'] else 'no'}</td><td>{_num(p['targeted'])}</td><td>{e(', '.join(p['caught_by']) or '–')}</td><td>{e(str(p['rank'] or '–'))}</td></tr>"
        for p in sc["seeded_per_flag"]
    ]
    parts.append("</tbody></table></section>")

    cov = r["coverage"]
    parts.append(
        f"<section><h2>Coverage</h2><p class=muted>{e(cov['by'])}</p><p>normal {_num(cov['normal'])} · boundary {_num(cov['boundary'])} · exception {_num(cov['exception'])}</p></section>"
    )

    parts.append(
        "<section><h2>Traceability</h2><table><thead><tr><th>requirement</th><th>criterion</th><th>case</th><th>assertions</th><th>gate</th><th>verdict (reference)</th><th>basis</th><th>evidence</th></tr></thead><tbody>"
    )
    parts += [
        f"<tr><td>{e(', '.join(t['req']))}</td><td>{e(t['ac'])}</td><td>{e(t['case'])}</td><td>{e(', '.join(t['assertions']))}</td><td>{e(t['gate'])}</td><td>{e(str(t['verdict']))} {e(str(t['dist']))}</td><td>{e(str(t.get('basis') or 'not recorded'))}</td><td><code>{e(str(t['evidence']))}</code></td></tr>"
        for t in r["traceability"]
    ]
    parts.append("</tbody></table></section>")

    u = r["uncovered"]
    lst = lambda xs: (
        "<ul>"
        + "".join(f"<li>{e(x if isinstance(x, str) else json.dumps(x))}</li>" for x in xs)
        + "</ul>"
        if xs
        else "<p>none</p>"
    )
    parts.append(f"""<section><h2>Not covered</h2><h4>Seeded race</h4><p>{e(u["seeded_race"] or "none")}</p>
<h4>Behaviour with no requirement</h4>{lst(u["behaviour_with_no_requirement"])}<h4>Criteria out of this run's scope</h4>{lst(u["criteria_out_of_scope"])}
<h4>Assumed criteria</h4>{lst(u["assumed"])}<h4>Signed cases not bound</h4>{lst(u["unbound_cases"])}<h4>Skipped templates and notes</h4>{lst(u["skipped_templates"])}<h4>Deferred</h4>{lst(u["deferred"])}</section>""")

    a = r["ai_decisions"]
    parts.append(f"""<section><h2>AI decisions</h2><table><tbody>
<tr><th>Spec answers</th><td>{_num(a["spec_answers"])}, assumed {_num(a["spec_assumed"])}</td></tr>
<tr><th>Cases drafted by the model</th><td>{_num(a["cases_llm_drafted"])}; sign-off: signed={e(str(a["cases_signoff"]["signed"]))}, approved {_num(a["cases_signoff"]["approved"])}</td></tr>
<tr><th>Gate equivalence proposals</th><td>equivalent {_num(a["gate_equivalence"]["equivalent"])}; {e(a["gate_equivalence"]["resolution"])}</td></tr>
<tr><th>Triage</th><td>{e(a["triage"])}</td></tr></tbody></table>""")
    parts.append(
        "<h4>Overrides</h4>"
        + (
            lst(
                [
                    f"{o['defect']}: {o['from']} → {o['to']} by {o['by']}: {o['reason']}"
                    for o in a["overrides"]
                ]
            )
        )
    )
    parts.append(
        "<h4>Review decisions</h4>"
        + lst(
            [
                f"#{d['item']} {d['kind']}: {d['decision']} by {d['by']}{' — ' + d['reason'] if d['reason'] else ''}"
                for d in a["review_decisions"]
            ]
        )
        + "</section>"
    )

    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Test report</title><style>{CSS}</style></head><body><main>{"".join(parts)}</main></body></html>"""


CSS = """:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#fff;--accent:#9a3412;--warn:#a16207}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--card:#1f1f1d;--accent:#fb923c;--warn:#facc15}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0}main{max-width:1100px;margin:auto;padding:16px}
h1{margin:.2em 0}h2{border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:2em}h3{margin:.2em 0}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto;margin:.5em 0}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
code,pre{font:12px/1.4 ui-monospace,monospace}pre{background:var(--card);border:1px solid var(--line);padding:8px;overflow-x:auto;white-space:pre-wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}
.label{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin-right:4px;font-size:12px}.warn{color:var(--warn)}
.muted{color:var(--muted);font-size:12px}details.num{display:inline-block}details.num summary{cursor:pointer;text-decoration:underline dotted}
.ids{font-size:12px;max-width:600px}details.ev{margin:4px 0}a{color:var(--accent)}"""


def summary_md(r: dict) -> str:
    w, sc = r["worklist"], r["scorecard"]
    out = [
        "# Test report — summary",
        "",
        f"Labels: {', '.join(r['header']['labels'])}",
        *(
            [f"Network: at run time {n['run']}; build: {n['build']}."]
            if (n := r["header"].get("network"))
            else []
        ),  # fmt: skip (blackbox D5)
        *([f"**{claim_line(c)}**"] if (c := r["header"].get("claim")) else []),
        *([f"Strongest claim: {h}."] if (h := r["header"].get("ceiling")) else []),
        "",
        "## P0 — defects",
    ]
    out += [f"- **{d['ac']}** {d['title']} ({d['kind']}{', ' + d['flag'] if d['flag'] else ''}): "
            f"{(d.get('observed') or {}).get('method', '')} {(d.get('observed') or {}).get('path', '')} → "
            f"{(d.get('observed') or {}).get('status', '')}; top suspect "
            f"`{d['suspects'][0]['file']}:{d['suspects'][0]['upstream_line']}`" if d["suspects"] else f"- **{d['ac']}** {d['title']}"
            for d in w["P0"]] or ["- none"]  # fmt: skip
    out += ["", "## P1 — untested or weak"] + [
        f"- {p['what']}: {p['case'] or p['ac']} → {p['closes_it']}" for p in w["P1"]
    ]
    out += [
        "",
        "## P2 — test health",
        (
            f"- redundant {_n(w['P2']['redundant'])}, mutation strength {_n(w['P2']['mutation_strength'])}, "
            f"health findings {len(w['P2']['findings'])}, quarantined {len(w['P2']['quarantine'])}"
        ),
    ]
    out += ["", "## P3 — spec and code drift"] + [
        f"- {p['what']}: {p['case'] or p['ac']} — {p['why']}" for p in w["P3"]
    ]
    c = r["checks"]
    if c["families"]:
        out += ["", "## Generic checks"] + [
            f"- {f}: {_n(v['cases'])} cases — " + ", ".join(f"{s} {_n(n)}" for s, n in v["outcomes"].items())
            for f, v in c["families"].items()
        ] + [f"- excluded ({k}): {_n(v)}" for k, v in c["excluded"].items()]  # fmt: skip
    if c["spec_mismatch"]:
        out += [
            "",
            f"## Server and spec disagree ({len(c['spec_mismatch']) + c['spec_mismatch_more']['n']} operations)",
        ]
        out += [f"- `{g['group']}`: {(g['observed'] or {}).get('request')} → {(g['observed'] or {}).get('status')}"
                f"{' (' + ', '.join(g['observed']['checks']) + ')' if (g['observed'] or {}).get('checks') else ''}"
                for g in c["spec_mismatch"]]  # fmt: skip
    if deps := r.get("dependencies"):
        out += ["", "## Outbound dependencies"] + [
            f"- {d['name']} ({d['kind']}): tested, simulated on {d['standin']}: "
            + ", ".join(f"{c['case']} {c['outcome']}" for c in d["checks"])
            + f"; received {len(d['received'])}"
            + (f"; reached through {', '.join(d['guards'])}" if d.get("guards") else "")
            if d["status"] == "tested"
            else f"- {d['name']} ({d['kind']}): not tested: {d['reason']}" for d in deps
        ]  # fmt: skip
    out += [
        "",
        "## Scorecard",
        f"- seeded detected (authored) {_n(sc['seeded_authored'])}; blind {sc['seeded_blind']}",
        f"- real defects {_n(sc['real_defects'])}",
        f"- triage correct on seeded rows {_n(sc['triage_correct_seeded'])}",
        *(f"- localisation {k} {_n(v)}" for k, v in sc["localisation"].items()),
        f"- fail before / pass after: {sc['fail_before_pass_after']}",
    ]
    return "\n".join(out) + "\n"


def cli(r: dict) -> str:
    w = r["worklist"]
    lines = [f"P0 defects: {len(w['P0'])}"]
    lines += [f"  {d['ac']:<5} {d['kind']:<6} {d['title']}" for d in w["P0"]]
    lines += [
        f"P1 untested/weak: {len(w['P1'])}",
        (
            f"P2 test health: {len(w['P2']['findings'])} finding(s), "
            f"mutation strength {_n(w['P2']['mutation_strength'])}"
        ),
        f"P3 spec/code drift: {len(w['P3'])}",
    ]
    c = r["checks"]
    if c["families"] or c["spec_mismatch"]:
        lines.append(f"Generic checks: {sum(v['cases']['n'] for v in c['families'].values())} case(s); "
                     f"server/spec disagree on {len(c['spec_mismatch']) + c['spec_mismatch_more']['n']}")  # fmt: skip
    return "\n".join(lines)
