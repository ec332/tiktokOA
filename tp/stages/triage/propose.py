"""Triage's one AI part (M8 triage D14): suggest a fix for a located defect. The model proposes
find/replace edits; fixed code checks each `find` occurs exactly once in the branch's file and
the result still parses, then builds the unified diff itself. Advisory: the report shows it,
and "Try suggested fix" proves it with a retest."""

import ast
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
MODEL, EFFORT = "claude-haiku-5-5", "medium"
STR = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["explanation", "edits"],
    "properties": {"explanation": STR, "edits": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["file", "find", "replace"],
        "properties": {"file": STR, "find": STR, "replace": STR}}}},
}  # fmt: skip


def prompt_hash() -> str:
    return hashlib.sha256((HERE / "prompts/fix.md").read_bytes() + MODEL.encode()).hexdigest()[:8]


def _numbered(text: str) -> str:
    return "\n".join(f"{i:>4}  {ln}" for i, ln in enumerate(text.splitlines(), 1))


def suggest(llm, defect: dict, criterion: dict | None, files: dict[str, str]) -> dict:
    """files: {repo path: branch text} for the suspects. -> {explanation, edits, patch, problems}."""
    crit = {k: (criterion or {}).get(k) for k in ("id", "title", "given", "when", "then",
                                                  "status_code")}  # fmt: skip
    p = (HERE / "prompts/fix.md").read_text()
    for k, v in {
        "CRITERION": json.dumps(crit, indent=1),
        "OBSERVED": json.dumps(defect.get("observed"), indent=1),
        "ASSERTIONS": json.dumps(defect.get("failed_assertions", []), indent=1),
        "ROOT_CAUSE": json.dumps(defect.get("root_cause") or "none (no server traceback)"),
        "SUSPECTS": json.dumps(defect.get("suspects", [])[:5], indent=1),
        "SOURCE": "\n".join(f"=== {f}\n{_numbered(t)}" for f, t in files.items()),
    }.items():
        p = p.replace("{{" + k + "}}", v)
    out = json.loads(llm.call(p, model=MODEL, schema=SCHEMA, effort=EFFORT, max_tokens=8000))
    patch, problems = build_patch(out["edits"], files)
    return {"explanation": out["explanation"], "edits": out["edits"], "patch": patch,
            "problems": problems, "applies": bool(patch) and not problems}  # fmt: skip


def build_patch(edits: list[dict], files: dict[str, str]) -> tuple[str, list[str]]:
    """The fixed check: every find occurs once in its file; Python still parses. -> (diff, problems)."""
    new, problems = dict(files), []
    for e in edits:
        text = new.get(e["file"])
        if text is None:
            problems.append(f"{e['file']}: not one of the suspect files")
            continue
        n = text.count(e["find"]) if e["find"] else 0
        if n != 1:
            problems.append(f"{e['file']}: find text occurs {n} times, needs exactly 1")
            continue
        new[e["file"]] = text.replace(e["find"], e["replace"], 1)
    for f, text in new.items():
        if text != files[f] and f.endswith(".py"):
            try:
                ast.parse(text)
            except SyntaxError as err:
                problems.append(
                    f"{f}: does not parse after the edit ({err.msg}, line {err.lineno})"
                )
    diff = "".join(
        "".join(
            difflib.unified_diff(
                files[f].splitlines(True), new[f].splitlines(True), f"a/{f}", f"b/{f}"
            )
        )
        for f in sorted(new)
        if new[f] != files[f]
    )
    return diff, problems
