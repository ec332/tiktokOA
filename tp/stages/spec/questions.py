"""QuestionFilter, QuestionWriter fallback, and the Integrator (plan Stage 1, P4–P5)."""

import hashlib
import re

from tp.core import review

PER_ROUND = 5  # † plan: ≤5 per round
ROUNDS = 2  # † plan: ≤2 rounds
INTENDED = "The current behaviour is intended"
HUMAN = "Leave to human judgement"
OUT_OF_SCOPE = "Out of scope: don't test this"
STATUS_IN_TEXT = re.compile(r"\b([1-5]\d\d)\b")


def key(criterion_title: str, about: str) -> str:
    norm = re.sub(r"\s+", " ", f"{criterion_title}|{about}".lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def earlier_answer(k: str) -> dict | None:
    """A question with this key already answered by a human in any run (the decision log)."""
    for item in reversed(review.list_(status="resolved")):
        p = item["payload"]
        if item["kind"] == "question" and p.get("key") == k and item["decision"] == "pick":
            choice = next(c for c in p["choices"] if c["id"] == item["detail"]["choice"])
            return {"item": item["id"], "text": choice["text"], "by": item["decided_by"]}
    return None


def candidates(investigated: list[dict], criteria: dict[str, dict]) -> list[dict]:
    """Investigator questions + one per suspected-defect observed criterion, unfiltered."""
    out = []
    for c in criteria.values():
        if c.get("suspected_defect") and c["status"] == "observed":
            options = [*c["alternatives"], f"{INTENDED}: {c['then']}"]
            out.append({"criterion": c["id"], "about": "intended behaviour", "kind": "defect",
                        "why": "The code's current behaviour looks wrong.", "options": options,
                        "changes": "expected_value"})  # fmt: skip
    for q in investigated:
        out.append({**q, "kind": "ambiguity"})
    return out


def filter_(cands: list[dict], criteria: dict[str, dict], asked: set[str]) -> tuple[list, list]:
    """-> (to ask, reused). Ask only if ≥2 distinct options change an expected value or a
    pass/fail; skip keys asked before in this spec; reuse answers from the decision log."""
    ask, reused, seen = [], [], set(asked)
    for q in cands:
        c = criteria.get(q.get("criterion", ""))
        options = list(dict.fromkeys(o.strip() for o in q.get("options", []) if o.strip()))
        if q.get("changes") == "none" or len(options) < 2:
            continue
        k = key(c["title"] if c else "", q["about"])
        if k in seen:
            continue
        seen.add(k)
        q = {**q, "key": k, "options": options, "criterion_text": _describe(c)}
        if prior := earlier_answer(k):
            reused.append({**q, "answer": prior})
        else:
            ask.append(q)
    ask.sort(key=lambda q: q["kind"] != "defect")  # suspected defects first
    return ask[:PER_ROUND], reused


def _describe(c: dict | None) -> str:
    return f"{c['title']}: given {c['given']}, when {c['when']}, then {c['then']}" if c else ""


def phrase(written: list[dict], asked: list[dict]) -> list[dict]:
    """Writer output where valid for that key, else a plain fallback from the options."""
    by_key = {w.get("key"): w for w in written}
    out = []
    for q in asked:
        w = by_key.get(q["key"])
        ids = [c.get("id") for c in w.get("choices", [])] if w else []
        if w and len(ids) >= 2 and len(set(ids)) == len(ids) and w.get("recommended") in ids:
            out.append({**q, "text": w["text"], "choices": w["choices"],
                        "recommended": w["recommended"]})  # fmt: skip
        else:
            choices = [{"id": chr(97 + i), "text": o} for i, o in enumerate(q["options"][:4])]
            text = f"{q['why']} ({q['about']})"
            out.append({**q, "text": text, "choices": choices, "recommended": "a"})
    return out


def _set_outcome(c: dict, text: str, defect_question: bool) -> None:
    c["resolution"] = text
    m = STATUS_IN_TEXT.search(text)
    if defect_question and not text.startswith(INTENDED):
        c["then"], c["status_code"] = text, int(m.group(1)) if m else None
    elif m:
        c["status_code"] = int(m.group(1))


def apply_answer(c: dict | None, q: dict, text: str, assumed: bool) -> dict | None:
    """Apply one answer to its criterion. Returns a change record for the diff."""
    if c is None:
        return None
    before = {k: c.get(k) for k in ("then", "status_code", "status")}
    if q["kind"] == "defect" or c["status"] == "draft" or q.get("changes") != "none":
        _set_outcome(c, text, q["kind"] == "defect")
    if q["kind"] == "defect" and not text.startswith(INTENDED):
        c["suspected_defect"] = True  # expected differs from what the code does today
    elif q["kind"] == "defect":
        c["suspected_defect"] = False
    c["status"] = "assumed" if assumed else "confirmed"
    c["review_needed"] = assumed or c.get("review_needed", False)
    after = {k: c.get(k) for k in ("then", "status_code", "status")}
    return {"criterion": c["id"], "before": before, "after": after, "answer": text}


def contradictions(changes: list[dict]) -> list[dict]:
    """Two answers in one round that set different status codes on the same criterion."""
    seen: dict[str, int] = {}
    out = []
    for ch in changes:
        sc = ch["after"]["status_code"]
        if sc is None:
            continue
        if ch["criterion"] in seen and seen[ch["criterion"]] != sc:
            out.append({"criterion": ch["criterion"], "status_codes": [seen[ch["criterion"]], sc]})
        seen[ch["criterion"]] = sc
    return out
