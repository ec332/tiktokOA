"""Deterministic case building (plan Stage 2): expected-value runner, expanders, differential
check, dedupe, budget, ranker, exit check, sign-off lines."""

import itertools
import re

from tp.core.hashing import sha256
from tp.stages.cases import calculators

EXTRAS_PER_CRITERION = 3  # † plan: LLM extras ≤3 per criterion
TECHNIQUE_ORDER = ["error_guessing", "decision_table", "boundary", "error_exception", "acceptance"]
HTTP = r"200|201|202|204|400|401|403|404|405|409|410|413|415|422|429|500|502|503"
_ACCEPT = re.compile(r"\b(accept(ed|s)?|succeeds?|created|returned|returns|can|is shown|listed|"
                     r"ok|updated|deleted|stored|receives?)\b")  # fmt: skip
_REFUSE = re.compile(r"\b(refused?|reject(ed|s)?|cannot|denied|forbidden|not allowed|fails?|"
                     r"error|invalid)\b")  # fmt: skip
_ACTOR = re.compile(r"\b(other users?|another user|owner|administrator|superuser|anonymous)\b")


def _text(c: dict) -> str:
    return " ".join(str(c.get(k) or "") for k in ("title", "then", "resolution")).lower()


_LIMIT = re.compile(
    r"\b(maximum|minimum|max|min|at most|at least|up to|longer than|limit|length)\b"
)
_RULE = re.compile(r"\b(must|required|mandatory)\b")


def traceable(c: dict) -> set[str]:
    """Literal outcomes the spec itself states for a criterion (design D9). A limit rule implies
    values within it succeed; a "must" rule implies a violation is refused."""
    text = _text(c)
    out = {m for m in re.findall(rf"\b({HTTP})\b", text)}
    if c.get("status_code"):
        out.add(str(c["status_code"]))
    if _ACCEPT.search(text) or _LIMIT.search(text) or any(s.startswith("2") for s in out):
        out.add("2xx")
    if _REFUSE.search(text) or _RULE.search(text) or any(s.startswith("4") for s in out):
        out.add("4xx")
    return out


def with_siblings(c: dict, siblings: list[dict]) -> dict:
    """A view of `c` whose stated outcomes also include those of criteria sharing a route:
    a decision table about C-8 ("others are refused") has rows C-7 states ("owners can")."""
    routes = set(c.get("routes", []))
    near = [s for s in siblings if s["id"] != c["id"] and routes & set(s.get("routes", []))]
    extra = " ".join(_text(s) + f" {s.get('status_code') or ''}" for s in near)
    return {**c, "resolution": f"{c.get('resolution') or ''} {extra}"}


def split_literal(value: str) -> tuple[str, str]:
    """ "403 'Not enough permissions'" -> ("403", "Not enough permissions")."""
    m = re.match(r"\s*([245]xx|\d{3})\b[\s:,.-]*(.*)", value)
    if not m:
        return value.strip(), ""
    return m.group(1), m.group(2).strip(" '\"()")


def literal(c: dict, value: str) -> tuple[str, str | None]:
    """-> (expected, note). An exact code the spec doesn't state falls back to its class. Prose
    after the code is kept only as a note; the oracle is the code."""
    value, prose = split_literal(value)
    if prose:
        stated = prose.lower() in _text(c)
        note = f"message {prose!r} {'stated by the spec' if stated else 'not in the spec: not checked'}"
        expected, cls_note = literal(c, value)
        return expected, "; ".join(n for n in (cls_note, note) if n)
    allowed = traceable(c)
    if value in allowed:
        return value, None
    cls = value[0] + "xx" if re.fullmatch(r"[245]\d\d", value) else None
    if cls and cls in allowed:
        return cls, f"spec states {cls}, not {value}: expecting the class"
    raise ValueError(
        f"expected {value!r} is not traceable to {c['id']} (spec allows {sorted(allowed)})"
    )


def _pairs(items: list[dict]) -> dict:
    return {p["name"]: p["value"] for p in items}


def _base(c: dict, flow: str, technique: str, d: dict) -> dict:
    return {"ac": c["id"], "flow": flow, "level": "api", "technique": technique,
            "template_refs": [technique] if technique != "acceptance" else [],
            "quality": "llm-drafted", "defect_hypothesis": d["defect_hypothesis"].strip(),
            "input_class": d["input_class"].strip(), "preconditions": list(d["preconditions"]),
            "steps": list(d["steps"]), "review_needed": bool(c.get("review_needed"))}  # fmt: skip


def build_unit(
    c: dict, flow: str, kind: str, d: dict, attention: list[str], siblings: list[dict] = ()
) -> dict | None:
    """One drafted item -> a case unit (a single case, a boundary group or a table group).
    Tables and boundaries span the rules of one route, so their outcomes may trace to siblings."""
    try:
        if kind == "case":
            return _case(c, flow, d, attention)
        if kind == "boundary":
            return _boundary(with_siblings(c, list(siblings)), flow, d, attention)
        return _table(with_siblings(c, list(siblings)), flow, d, attention)
    except (ValueError, KeyError, TypeError) as e:
        attention.append(f"{c['id']}: dropped a drafted {kind}: {e}")
        return None


def _case(c, flow, d, attention) -> dict:
    u = _base(c, flow, d["technique"], d) | {"purpose": d["purpose"], "data": _pairs(d["data"])}
    if d["expected_kind"] == "calculator":
        params = _pairs(d["params"])
        if d["calculator"] not in calculators.CALCULATORS:
            raise ValueError(f"unknown calculator {d['calculator']!r}")
        value = calculators.run(d["calculator"], params)
        u["expected"] = {
            "value": value,
            "oracle": f"calculator:{d['calculator']}",
            "params": params,
        }
        if d["calculator"] == "page_window":  # differential check against the property
            args = {k: int(params[k]) for k in ("total", "skip", "limit")}
            if not calculators.PROPERTIES["page_len_ok"](**args, data_len=value["data_len"]):
                attention.append(f"{c['id']}: page_window disagrees with property page_len_ok")
    else:
        value, note = literal(c, d["expected_value"])
        u["expected"] = {"value": value, "oracle": "literal"}
        if note:
            u["notes"] = [note]
    return u


def _boundary(c, flow, d, attention) -> dict:
    # A limit rule implies its allowed side succeeds: "longer than 255 is refused" -> 255 is ok.
    ok, ok_note = ("2xx", None) if d["ok"].strip() == "2xx" else literal(c, d["ok"])
    fail, fail_note = literal(c, d["fail"])
    points = calculators.run("boundary_points", {"limit": d["limit"]})
    outcomes = [calculators.run("boundary_expect", {"value": v, "limit": d["limit"],
                "inclusive": d["inclusive"], "ok": ok, "fail": fail,
                "direction": d["direction"]}) for v in points]  # fmt: skip
    if not calculators.PROPERTIES["boundary_monotonic"](points, outcomes, ok, d["direction"]):
        attention.append(f"{c['id']}: boundary outcomes are not monotonic")
    u = _base(c, flow, "boundary", d) | {
        "purpose": f"{d['field']} at the {d['limit']} limit",
        "boundary": {"field": d["field"], "limit": d["limit"], "inclusive": d["inclusive"],
                     "direction": d["direction"], "inclusive_reason": d["inclusive_reason"]},
        "points": [{"value": v, "data": {d["field"]: v}, "expected": {"value": o,
                    "oracle": "calculator:boundary_expect"}} for v, o in zip(points, outcomes,
                                                                              strict=True)],
    }  # fmt: skip
    u["notes"] = [n for n in (ok_note, fail_note) if n]
    if not c.get("status_code") and "inclusive" not in _text(c):
        u["review_needed"] = True  # inclusivity was the drafter's choice, not the spec's
    return u


def _table(c, flow, d, attention) -> dict:
    rows = []
    for r in d["rows"]:
        if len(r["values"]) != len(d["conditions"]):
            raise ValueError("a decision-table row has the wrong number of values")
        value, _ = literal(c, r["expected"])
        rows.append({"when": dict(zip(d["conditions"], r["values"], strict=True)),
                     "expected": {"value": value, "oracle": "literal"}})  # fmt: skip
    seen = [sorted({row["when"][k] for row in rows}) for k in d["conditions"]]
    combos = {tuple(row["when"][k] for k in d["conditions"]) for row in rows}
    missing = [m for m in itertools.product(*seen) if m not in combos]
    if missing:
        attention.append(f"{c['id']}: decision table leaves out {len(missing)} combination(s), "
                         f"e.g. {dict(zip(d['conditions'], missing[0], strict=True))}")  # fmt: skip
    return _base(c, flow, "decision_table", d) | {
        "purpose": f"{' × '.join(d['conditions'])}",
        "conditions": d["conditions"],
        "rows": rows,
    }


def units_from_draft(c: dict, flow: str, draft: dict, attention: list[str], siblings=()) -> list:
    units = [build_unit(c, flow, "case", d, attention) for d in draft["cases"]]
    units += [build_unit(c, flow, "boundary", d, attention, siblings) for d in draft["boundaries"]]
    units += [build_unit(c, flow, "table", d, attention, siblings) for d in draft["tables"]]
    return [u for u in units if u]


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def dedupe_and_budget(units: list[dict], minimum: dict[str, list[str]]) -> tuple[list, list]:
    """-> (kept, deferred). Same criterion+technique+input class+defect = one case; no two kept
    cases share a defect hypothesis; minimum-template cases uncapped, extras ≤3 per criterion."""
    kept, deferred, seen_key, seen_hyp, extras = [], [], set(), {}, {}
    for u in units:
        key = (u["ac"], u["technique"], _norm(u["input_class"]), _norm(u["defect_hypothesis"]))
        hyp = _norm(u["defect_hypothesis"])
        if key in seen_key or hyp in seen_hyp:
            deferred.append(
                {**u, "deferred": f"duplicate of a case for {seen_hyp.get(hyp, u['ac'])}"}
            )
            continue
        required = u["technique"] == "acceptance" or u["technique"] in minimum.get(u["ac"], [])
        if not required:
            extras[u["ac"]] = extras.get(u["ac"], 0) + 1
            if extras[u["ac"]] > EXTRAS_PER_CRITERION:
                deferred.append({**u, "deferred": "over the extras budget for this criterion"})
                continue
        seen_key.add(key)
        seen_hyp[hyp] = u["ac"]
        kept.append(u)
    return kept, deferred


def rank(units: list[dict], criteria: dict[str, dict], findings: list[dict]) -> None:
    """Additive flags (plan): candidate defect 5, permission 4, rule interaction 3, observed 3,
    default 1. Sets score/priority and sorts in place; ties by technique order, then criterion."""
    interacting = {
        cid for f in findings if f["kind"] == "rule_interaction" for cid in f["criteria"]
    }
    for u in units:
        c = criteria[u["ac"]]
        score = 1
        score += 5 if c.get("suspected_defect") else 0
        score += 4 if u["technique"] == "decision_table" or _ACTOR.search(_text(c)) else 0
        score += 3 if u["ac"] in interacting else 0
        score += 3 if u["ac"].startswith("O-") else 0
        u["score"] = score
        u["priority"] = "P0" if score >= 8 else "P1" if score >= 5 else "P2" if score >= 3 else "P3"
    order = {t: i for i, t in enumerate(TECHNIQUE_ORDER)}
    units.sort(key=lambda u: (-u["score"], order[u["technique"]], u["ac"]))


def assign_ids(units: list[dict]) -> None:
    n: dict[str, int] = {}
    for u in units:
        n[u["ac"]] = n.get(u["ac"], 0) + 1
        u["id"] = f"TC-{u['ac'].replace('-', '')}-{n[u['ac']]}"
        assign_point_ids(u)


def assign_point_ids(u: dict) -> None:
    for i, p in enumerate(u.get("points", [])):
        p["id"] = f"{u['id']}.{'abc'[i]}"
    for i, r in enumerate(u.get("rows", []), 1):
        r["id"] = f"{u['id']}.{i}"


def exit_problems(units: list[dict], scope: list[dict], minimum: dict, drafts: dict) -> list[str]:
    """Plan ExitCheck. Problems become attention items in the sign-off, not a silent failure."""
    out = []
    for c in scope:
        mine = [u for u in units if u["ac"] == c["id"]]
        used = {u["technique"] for u in mine}
        skipped = {s["template"] for s in drafts.get(c["id"], {}).get("skipped_templates", [])
                   if s["reason"].strip()}  # fmt: skip
        if "acceptance" not in used:
            out.append(f"{c['id']}: no acceptance case")
        for t in minimum[c["id"]]:
            if t not in used and t not in skipped:
                out.append(
                    f"{c['id']}: minimum template {t} neither used nor skipped with a reason"
                )
        refusing = (c.get("status_code") or 0) >= 400 or _REFUSE.search(_text(c))
        if refusing and not used & {"error_exception", "error_guessing", "decision_table"}:
            out.append(f"{c['id']}: error path with no exception case")
    for u in units:
        missing = [k for k in ("defect_hypothesis", "level") if not u.get(k)]
        if "expected" not in u and "points" not in u and "rows" not in u:
            missing.append("expected")
        if missing:
            out.append(f"{u['id']}: missing {missing}")
    return out


def line(u: dict) -> dict:
    """One sign-off line per case unit. The hash covers everything a reviewer approves."""
    if "points" in u:
        exp = ", ".join(f"{p['value']}→{p['expected']['value']}" for p in u["points"])
    elif "rows" in u:
        exp = f"{len(u['rows'])} rows"
    else:
        exp = f"{u['expected']['value']} [{u['expected']['oracle']}]"
    body = {k: v for k, v in u.items() if k not in ("score",)}
    text = f"{u['priority']} {u['technique']}: {u['purpose']} → {exp}. Catches: {u['defect_hypothesis']}"
    return {"id": u["id"], "text": text, "flow": u["flow"], "hash": sha256(body)}
