"""Localiser (deterministic chain, design D6–D9) and LocalisationScorer (D10).

Chain: failing assertion → its HTTP exchange → the trace log line → the traceback after it →
innermost app frame → the request parameter in the handler's signature → the clause the DB
error names → the criterion's code refs. At most 5 suspects, each with its basis."""

import ast
import difflib
import re

from tp.stages.triage.evidence import repo_path

MAX_SUSPECTS = 5
# Postgres errors (psycopg class name → SQLSTATE, the SQL call it points at)
DB_ERRORS = {
    "InvalidRowCountInResultOffsetClause": ("2201X", ".offset("),
    "InvalidRowCountInLimitClause": ("2201W", ".limit("),
    "NotNullViolation": ("23502", None),
}


def line_map(upstream: str, patched: str) -> tuple[dict[int, int], dict[int, int]]:
    """Line numbers both ways between the pinned upstream file and the patched one. A changed
    line maps to the start of its replacement block."""
    up, pa = upstream.splitlines(), patched.splitlines()
    to_up: dict[int, int] = {}
    to_pa: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, up, pa, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                to_up[j1 + k + 1], to_pa[i1 + k + 1] = i1 + k + 1, j1 + k + 1
            continue
        if tag == "replace":  # patch-added lines (insert) have no upstream line
            to_up |= {p + 1: i1 + 1 for p in range(j1, j2)}
        to_pa |= {u + 1: j1 + 1 for u in range(i1, i2)}  # replace/delete
    return to_up, to_pa


def _function(source: str, line: int):
    best = None
    for node in ast.walk(ast.parse(source)):
        is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if (
            is_fn
            and node.lineno <= line <= node.end_lineno
            and (best is None or node.lineno > best.lineno)
        ):
            best = node
    return best


class Source:
    """read(rel) -> (patched_text, upstream_text) for a repo-relative path, or None."""

    def __init__(self, read):
        self._read, self._cache = read, {}

    def get(self, rel: str):
        if rel not in self._cache:
            self._cache[rel] = self._read(rel)
        return self._cache[rel]

    def suspect(self, rel: str, *, patched: int | None = None, upstream: int | None = None,
                basis: str) -> dict | None:  # fmt: skip
        got = self.get(rel)
        if not got:
            return {
                "file": rel,
                "line": patched,
                "upstream_line": upstream,
                "text": None,
                "basis": basis,
            }
        pa_text, up_text = got
        to_up, to_pa = line_map(up_text, pa_text)
        patched = patched or to_pa.get(upstream)
        upstream = upstream or to_up.get(patched)
        lines = pa_text.splitlines()
        text = lines[patched - 1].strip() if patched and patched <= len(lines) else None
        return {
            "file": rel,
            "line": patched,
            "upstream_line": upstream,
            "text": text,
            "basis": basis,
        }


def handler(files: dict[str, str], method: str, path: str) -> tuple[str, int, str] | None:
    """(file, def line, name) of the route function that served `METHOD path`, from the
    `APIRouter(prefix=...)` + `@router.<method>("...")` declarations.
    shortcut: FastAPI's decorator style only; other frameworks need their own reader."""
    for rel, text in sorted(files.items()):
        prefix = m.group(1) if (m := re.search(r'APIRouter\([^)]*prefix="([^"]*)"', text)) else ""
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            d = re.match(rf'\s*@router\.{method.lower()}\(\s*"([^"]*)"', line)
            if not d:
                continue
            pattern = re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(prefix + d.group(1)))
            if re.fullmatch(rf".*{pattern}/?", path):
                fn = _function(
                    text, next(k for k in range(n, len(lines) + 1) if "def " in lines[k - 1])
                )
                return rel, fn.lineno, fn.name
    return None


def suspects(failing: dict, log: dict | None, criterion: dict | None, src: Source,
             files: dict[str, str] | None = None) -> list[dict]:  # fmt: skip
    """failing: the first failing record of the defect; log: EvidenceIndex.log() of its exchange;
    files: the patched app source (for the route → handler link)."""
    out: list[dict] = []
    tb = (log or {}).get("traceback")
    exchange = _failing_exchange(failing)
    if tb and tb["app_frames"]:
        frame = tb["app_frames"][-1]
        rel = repo_path(frame["path"])
        got = src.get(rel)
        fn = _function(got[0], frame["line"]) if got else None
        cause = tb.get("root_cause") or {}
        cls = cause.get("cls", "").rsplit(".", 1)[-1]
        if fn and exchange:
            sig_lines = range(fn.lineno, (fn.body[0].lineno if fn.body else fn.lineno))
            for param, value in (exchange.get("query") or {}).items():
                hit = next(
                    (
                        n
                        for n in sig_lines
                        if re.search(rf"\b{re.escape(param)}\s*:", got[0].splitlines()[n - 1])
                    ),
                    None,
                )
                if hit:
                    out.append(src.suspect(rel, patched=hit, basis=(
                        f"request parameter {param}={value} reaches the signature of {fn.name}() "
                        f"with no bound; traceback ends in {cause.get('cls')}")))  # fmt: skip
        if cls in DB_ERRORS and fn and DB_ERRORS[cls][1]:
            sqlstate, call = DB_ERRORS[cls]
            body = got[0].splitlines()
            # the call in the statement that ran: the last one before the failing frame
            calls = [n for n in range(fn.lineno, frame["line"]) if call in body[n - 1]]
            if calls:
                out.append(src.suspect(rel, patched=calls[-1], basis=(
                    f"{cause['cls']} (SQLSTATE {sqlstate}): \"{cause['msg']}\" names this clause")))  # fmt: skip
        out.append(src.suspect(rel, patched=frame["line"], basis=(
            f"innermost app frame of the traceback ({frame['fn']}), "
            f"{cause.get('cls', 'error')} raised here")))  # fmt: skip
    if exchange and files and (h := handler(files, exchange["method"], exchange["path"])):
        out.append(src.suspect(h[0], patched=h[1], basis=(
            f"handler of the failing request {exchange['method']} {exchange['path']} ({h[2]}), "
            f"which answered {exchange['status']}")))  # fmt: skip
    for ref in [s for s in (criterion or {}).get("sources", []) if s.get("kind") == "code"]:
        out.append(src.suspect(ref["file"], upstream=ref["line"],
                               basis=f"code_ref of {criterion['id']} (no stack)" if not tb
                               else f"code_ref of {criterion['id']}"))  # fmt: skip
    seen, unique = set(), []
    for s in out:
        key = (s["file"], s["upstream_line"] or s["line"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:MAX_SUSPECTS]


def _failing_exchange(row: dict) -> dict | None:
    steps = [e for e in row.get("exchanges", []) if not e.get("setup")]
    return steps[-1] if steps else None


def flag_sites(src_files: dict[str, str], flag: str) -> list[dict]:
    """Where a seeded flag is read in the patched source (the scorer's ground truth, never an
    input to the localiser)."""
    return [{"file": rel, "line": n} for rel, text in sorted(src_files.items())
            for n, line in enumerate(text.splitlines(), 1) if f"settings.{flag}" in line]  # fmt: skip


def rank(suspects_: list[dict], sites: list[dict], files: dict[str, str]) -> int | None:
    """1-based rank of the first suspect inside the function that reads the flag, else None."""
    spans = []
    for t in sites:
        fn = _function(files[t["file"]], t["line"]) if t["file"] in files else None
        spans.append(
            (t["file"], fn.lineno, fn.end_lineno) if fn else (t["file"], t["line"], t["line"])
        )
    for i, s in enumerate(suspects_, 1):
        if s["line"] and any(s["file"] == f and a <= s["line"] <= b for f, a, b in spans):
            return i
    return None
