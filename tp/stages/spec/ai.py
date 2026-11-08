"""The three AI components (design D1): one structured-output call each, through ctx.llm."""

import hashlib
import json
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"
# The smallest model that meets the goal (user: keep API cost minimal). Measured on the story
# fixture: Haiku 5.5 @ medium covered every story bullet and found the P3 targets for $0.011 a
# run; Opus 5.5 @ high cost $0.33 and found fewer. See docs/status/spec/004.
EXTRACT_MODEL = INVESTIGATE_MODEL = WRITER_MODEL = "claude-haiku-5-5"
EXTRACT_EFFORT = INVESTIGATE_EFFORT = "medium"


def prompt_hash() -> str:
    """Prompts + model choices: changing either changes the stage's behaviour (design D8)."""
    h = hashlib.sha256()
    for p in sorted(PROMPTS.glob("*.md")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    models = [EXTRACT_MODEL, EXTRACT_EFFORT, INVESTIGATE_MODEL, INVESTIGATE_EFFORT, WRITER_MODEL]
    h.update("|".join(models).encode())
    return h.hexdigest()[:8]


def _prompt(name: str, **fields: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    for k, v in fields.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required or list(props),
        "additionalProperties": False,
    }


STR, BOOL = {"type": "string"}, {"type": "boolean"}
STRS = {"type": "array", "items": STR}
STATUS = {"type": ["integer", "null"]}
EVIDENCE = {"type": "array", "items": _obj({"file": STR, "line": {"type": "integer"}})}
_CRITERION = {
    "title": STR, "actor": STR, "given": STR, "when": STR, "then": STR,
    "status_code": STATUS, "routes": STRS,
}  # fmt: skip

EXTRACT_SCHEMA = _obj(
    {
        "criteria": {
            "type": "array",
            "items": _obj({**_CRITERION, "human_judgement": BOOL, "source_quote": STR}),
        }
    }
)
INVESTIGATE_SCHEMA = _obj(
    {
        "criterion_routes": {
            "type": "array",
            "items": _obj({"criterion": STR, "routes": STRS}),
        },
        "findings": {
            "type": "array",
            "items": _obj(
                {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "missing_error_path",
                            "rule_interaction",
                            "story_code_conflict",
                            "suspected_defect",
                            "no_requirement",
                        ],
                    },
                    "summary": STR,
                    "evidence": EVIDENCE,
                    "criteria": STRS,
                }
            ),
        },
        "observed": {
            "type": "array",
            "items": _obj(
                {**_CRITERION, "evidence": EVIDENCE, "suspected_defect": BOOL, "alternatives": STRS}
            ),
        },
        "questions": {
            "type": "array",
            "items": _obj(
                {
                    "criterion": STR,
                    "about": STR,
                    "why": STR,
                    "options": STRS,
                    "changes": {"type": "string", "enum": ["expected_value", "pass_fail", "none"]},
                }
            ),
        },
    }
)
WRITER_SCHEMA = _obj(
    {
        "questions": {
            "type": "array",
            "items": _obj(
                {
                    "key": STR,
                    "text": STR,
                    "choices": {"type": "array", "items": _obj({"id": STR, "text": STR})},
                    "recommended": STR,
                    "settled_by": STR,
                }
            ),
        }
    }
)

OBSERVED = {
    "story": "Only add observed criteria for important behaviour the story leaves out.",
    "brief": (
        "The brief is vague, so most criteria will come from here: cover the behaviour of the "
        "routes relevant to the brief, and only those."
    ),
}


def _call(llm, prompt: str, model: str, schema: dict, effort: str, max_tokens: int = 16000):
    raw = llm.call(prompt, model=model, schema=schema, effort=effort, max_tokens=max_tokens)
    return json.loads(raw)


def extract(llm, kind: str, requirements: str) -> list[dict]:
    p = _prompt("extractor", KIND=kind, REQUIREMENTS=requirements)
    return _call(llm, p, EXTRACT_MODEL, EXTRACT_SCHEMA, EXTRACT_EFFORT)["criteria"]


def rules_text(extra: list[str] | None = None) -> str:
    """rules.md, then this run's added rules (M8 spec D13). The defaults always apply."""
    base = (PROMPTS / "rules.md").read_text().rstrip()
    if not extra:
        return base
    return base + "\n\nRules added for this run:\n" + "\n".join(f"- {r}" for r in extra)


def default_rules() -> list[str]:
    return [ln[2:].strip() for ln in (PROMPTS / "rules.md").read_text().splitlines()
            if ln.startswith("- ")]  # fmt: skip


SUGGEST_SCHEMA = _obj({"rules": {"type": "array", "items": _obj(
    {"rule": {"type": "string"}, "why": {"type": "string"}})}})  # fmt: skip


def suggest_rules(llm, overview: str, routes: list[str], source: str) -> list[dict]:
    """M8 spec D14: feature-specific rule suggestions. Shown unticked; never applied by itself."""
    p = _prompt("rule_suggester", RULES=rules_text(), OVERVIEW=overview,
                ROUTES="\n".join(routes) or "(none chosen)", SOURCE=source)  # fmt: skip
    return _call(llm, p, WRITER_MODEL, SUGGEST_SCHEMA, "low", 4000)["rules"][:6]


def investigate(llm, kind: str, requirements: str, criteria, index, source, flagged,
                extra_rules: list[str] | None = None) -> dict:  # fmt: skip
    view = [{k: c[k] for k in ("id", "title", "actor", "given", "when", "then", "status_code")}
            for c in criteria]  # fmt: skip
    p = _prompt(
        "investigator",
        KIND=kind,
        OBSERVED_GUIDANCE=OBSERVED[kind],
        RULES=rules_text(extra_rules),
        FLAGGED=json.dumps(flagged) if flagged else "none",
        REQUIREMENTS=requirements,
        CRITERIA=json.dumps(view, indent=1),
        INDEX=json.dumps(index["routes"], indent=1),
        SOURCE=source,
    )
    return _call(llm, p, INVESTIGATE_MODEL, INVESTIGATE_SCHEMA, INVESTIGATE_EFFORT, 32000)


def write_questions(llm, questions: list[dict], decided: list[dict] | None = None) -> list[dict]:
    """decided: this run's answers so far ({key, about, answer}); the writer keeps its
    recommendations consistent with them and marks questions they already settle (demo/006)."""
    view = [{k: q[k] for k in ("key", "about", "why", "options", "criterion_text")}
            for q in questions]  # fmt: skip
    done = [{k: d[k] for k in ("key", "about", "answer")} for d in decided or []]
    p = _prompt("question_writer", QUESTIONS=json.dumps(view, indent=1),
                DECIDED=json.dumps(done, indent=1))  # fmt: skip
    return _call(llm, p, WRITER_MODEL, WRITER_SCHEMA, "low")["questions"]
