"""Deterministic checks: SchemaCheck, AmbiguityLint (design D6 taxonomy), ExitCheck."""

import re

STATUSES = ("draft", "confirmed", "assumed", "observed", "out_of_scope")
ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S*$")

AMBIGUITY = (
    "vague_quantifier",
    "missing_value",
    "no_observable_outcome",
    "undefined_term",
    "missing_actor",
    "missing_error_path",
    "unspecified_boundary",
    "conflicting_statements",
    "implicit_assumption",
)
# Tags that leave the expected outcome undecided: the criterion stays `draft` until answered.
BLOCKING = {
    "vague_quantifier",
    "missing_value",
    "no_observable_outcome",
    "undefined_term",
    "unspecified_boundary",
    "conflicting_statements",
}

_WORDS = {
    "vague_quantifier": r"\b(some|many|few|several|large|small|fast|quickly|slow|reasonable|too long)\b",
    "undefined_term": r"\b(valid|invalid|appropriate|proper|correct|normal|suitable)\b",
    "implicit_assumption": r"\b(as usual|etc\.?|and so on|standard|the usual)\b",
}
_LIMIT = r"\b(maximum|minimum|max|min|limit|cap|at most|at least|up to|longer than|shorter than)\b"
_NUMBER = r"\b\d+\b"
_BOUNDARY = (
    r"\b(inclusive|exclusive|up to and including|at most|at least|or more|or fewer|or less)\b"
)
_ACTOR = r"\b(anonymous|unauthenticated|user|owner|superuser|admin|anyone|logged[- ]in|caller)\b"
_FAILURE = r"\b(4\d\d|5\d\d|error|reject|rejected|fails?|denied|forbidden|not found|invalid)\b"
_OUTCOME = (
    r"\b(\d{3}|returns?|responds?|response|status|is (created|deleted|updated|stored)|list|count)\b"
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """1-based (first, last) line of `quote` in `text`, whitespace- and case-insensitive."""
    q = _norm(quote)
    if not q:
        return None
    flat, starts = "", []  # flat normalised text; starts[i] = (offset in flat, line number)
    for no, line in enumerate(text.splitlines(), 1):
        n = _norm(line)
        if not n:
            continue
        if flat:
            flat += " "
        starts.append((len(flat), no))
        flat += n
    at = flat.find(q)
    if at < 0:
        return None

    def line_at(offset: int) -> int:
        return max(no for start, no in starts if start <= offset)

    return line_at(at), line_at(at + len(q) - 1)


def _criterion_fields(item: dict) -> list[str]:
    problems = []
    for f in ("title", "when", "then"):
        if not str(item.get(f, "")).strip():
            problems.append(f"empty {f}")
    sc = item.get("status_code")
    if sc is not None and not (isinstance(sc, int) and 100 <= sc <= 599):
        problems.append(f"bad status_code {sc!r}")
    for r in item.get("routes", []):
        if not ROUTE.match(r):
            problems.append(f"bad route {r!r}")
    return problems


def check_extracted(items: list[dict], requirements: str) -> tuple[list[dict], list[dict]]:
    """Extractor output -> (criteria, rejected). A criterion needs a quote found in the text."""
    kept, rejected = [], []
    for item in items:
        problems = _criterion_fields(item)
        span = find_quote(requirements, item.get("source_quote", ""))
        if span is None:
            problems.append("source_quote not found in the requirements")
        if problems:
            rejected.append({"item": item, "reasons": problems})
            continue
        kept.append(
            {
                **{k: item[k] for k in ("title", "actor", "given", "when", "then")},
                "status_code": item.get("status_code"),
                "human_judgement": bool(item.get("human_judgement")),
                "routes": list(item.get("routes", [])),
                "sources": [
                    {
                        "kind": "requirement",
                        "lines": f"L{span[0]}-L{span[1]}",
                        "quote": item["source_quote"],
                    }
                ],
            }
        )
    return kept, rejected


def check_observed(items: list[dict], files: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Investigator's code-derived criteria -> (criteria, rejected). Evidence must exist."""
    kept, rejected = [], []
    for item in items:
        problems = _criterion_fields(item)
        evidence = item.get("evidence", [])
        if not evidence:
            problems.append("no code evidence")
        for e in evidence:
            if not (e.get("file") in files and 1 <= int(e.get("line", 0)) <= files[e["file"]]):
                problems.append(f"evidence {e.get('file')}:{e.get('line')} does not exist")
        if problems:
            rejected.append({"item": item, "reasons": problems})
            continue
        kept.append(
            {
                **{k: item[k] for k in ("title", "actor", "given", "when", "then")},
                "status_code": item.get("status_code"),
                "human_judgement": False,
                "routes": list(item.get("routes", [])),
                "sources": [
                    {"kind": "code", "file": e["file"], "line": int(e["line"])} for e in evidence
                ],
                "suspected_defect": bool(item.get("suspected_defect")),
                "alternatives": list(item.get("alternatives", [])),
            }
        )
    return kept, rejected


def lint(c: dict) -> list[str]:
    """AmbiguityLint for one criterion (conflicting_statements is added by lint_all)."""
    text = " ".join(str(c.get(k, "")) for k in ("title", "given", "when", "then")).lower()
    then = str(c.get("then", "")).lower()
    tags = [t for t, rx in _WORDS.items() if re.search(rx, text)]
    if re.search(_LIMIT, text) and not re.search(_NUMBER, text):
        tags.append("missing_value")
    if re.search(_LIMIT, text) and re.search(_NUMBER, text) and not re.search(_BOUNDARY, text):
        tags.append("unspecified_boundary")
    if c.get("status_code") is None and not re.search(_OUTCOME, then):
        tags.append("no_observable_outcome")
    if not re.search(_ACTOR, " ".join(str(c.get(k, "")) for k in ("actor", "given")).lower()):
        tags.append("missing_actor")
    if not re.search(_FAILURE, text) and (c.get("status_code") or 200) < 400:
        tags.append("missing_error_path")
    return sorted(set(tags), key=AMBIGUITY.index)


def lint_all(criteria: list[dict]) -> None:
    """Tag every criterion in place; same `when` with different outcomes = conflicting."""
    for c in criteria:
        c["ambiguity"] = lint(c)
    by_when: dict[str, list[dict]] = {}
    for c in criteria:
        by_when.setdefault(_norm(c["when"]) + "|" + _norm(c.get("given", "")), []).append(c)
    for group in by_when.values():
        outcomes = {(c.get("status_code"), _norm(c["then"])) for c in group}
        if len(outcomes) > 1:
            for c in group:
                c["ambiguity"] = sorted(
                    set(c["ambiguity"]) | {"conflicting_statements"}, key=AMBIGUITY.index
                )


def initial_status(c: dict) -> str:
    if c.get("human_judgement"):
        return "confirmed"
    return "draft" if BLOCKING & set(c.get("ambiguity", [])) else "confirmed"


def exit_check(criteria: list[dict], open_questions: int) -> list[str]:
    """Empty list = the spec may be frozen."""
    problems = [f"{open_questions} question(s) still open"] if open_questions else []
    for c in criteria:
        if c["status"] not in STATUSES:
            problems.append(f"{c['id']}: unknown status {c['status']}")
        if c["status"] in ("draft", "observed"):
            problems.append(f"{c['id']}: still {c['status']}")
        if c["status"] == "out_of_scope":
            continue
        if not c["sources"]:
            problems.append(f"{c['id']}: no source")
        if not (c.get("human_judgement") or c.get("status_code") or str(c.get("then", "")).strip()):
            problems.append(f"{c['id']}: no expected outcome and not human_judgement")
    return problems
