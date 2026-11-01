"""The cases stage's AI calls (design D3): one drafting call, plus a revision call on rejection."""

import hashlib
import json
from pathlib import Path

import yaml

from tp.stages.cases import calculators

HERE = Path(__file__).parent
MODEL, EFFORT = "claude-haiku-5-5", "medium"  # smallest model that does the job (spec status 004)
FACTORIES = ["user", "superuser", "item", "inactive_user"]  # env stage factories (env E5)


def catalogue() -> dict[str, dict]:
    return {
        p.stem: yaml.safe_load(p.read_text()) for p in sorted((HERE / "templates").glob("*.yaml"))
    }


def version_hash() -> str:
    """Prompts, templates, calculators and the model: any change bumps the stage version."""
    h = hashlib.sha256()
    for p in sorted([*HERE.glob("prompts/*.md"), *HERE.glob("templates/*.yaml"),
                     HERE / "calculators/__init__.py"]):  # fmt: skip
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    h.update(f"{MODEL}|{EFFORT}".encode())
    return h.hexdigest()[:8]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}  # fmt: skip


STR, BOOL, INT = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}
STRS = {"type": "array", "items": STR}
PAIRS = {"type": "array", "items": _obj({"name": STR, "value": STR})}
COMMON = {"defect_hypothesis": STR, "input_class": STR, "preconditions": STRS, "steps": STRS}
CASE = _obj({
    "technique": {"type": "string",
                  "enum": ["acceptance", "error_exception", "error_guessing"]},
    "purpose": STR, **COMMON, "data": PAIRS,
    "expected_kind": {"type": "string", "enum": ["literal", "calculator"]},
    "expected_value": STR, "calculator": STR, "params": PAIRS,
})  # fmt: skip
BOUNDARY = _obj({**COMMON, "field": STR, "limit": INT, "inclusive": BOOL,
                 "direction": {"type": "string", "enum": ["max", "min"]},
                 "inclusive_reason": STR, "ok": STR, "fail": STR})  # fmt: skip
TABLE = _obj({**COMMON, "conditions": STRS,
              "rows": {"type": "array", "items": _obj({"values": STRS, "expected": STR})}})  # fmt: skip
CHOICE = {"type": "array", "items": _obj({"template": STR, "reason": STR})}
DRAFT_SCHEMA = _obj({"criteria": {"type": "array", "items": _obj({
    "criterion": STR, "extra_templates": CHOICE, "skipped_templates": CHOICE,
    "cases": {"type": "array", "items": CASE},
    "boundaries": {"type": "array", "items": BOUNDARY},
    "tables": {"type": "array", "items": TABLE},
})}})  # fmt: skip
REVISE_SCHEMA = _obj(
    {  # each revised unit keeps its id and goes in the list of its shape
        "cases": {"type": "array", "items": _obj({"id": STR, **CASE["properties"]})},
        "boundaries": {"type": "array", "items": _obj({"id": STR, **BOUNDARY["properties"]})},
        "tables": {"type": "array", "items": _obj({"id": STR, **TABLE["properties"]})},
    }
)


def _prompt(name: str, **fields) -> str:
    text = (HERE / "prompts" / f"{name}.md").read_text()
    for k, v in fields.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def draft(llm, criteria: list[dict], minimum: dict[str, list[str]]) -> list[dict]:
    view = [{"id": c["id"], "title": c["title"], "actor": c.get("actor", ""), "given": c["given"],
             "when": c["when"], "then": c["then"], "status_code": c.get("status_code"),
             "resolution": c.get("resolution"), "suspected_defect": c.get("suspected_defect", False),
             "status": c["status"], "minimum": minimum[c["id"]]} for c in criteria]  # fmt: skip
    p = _prompt("drafter", FACTORIES=", ".join(FACTORIES),
                TEMPLATES=yaml.safe_dump(list(catalogue().values()), sort_keys=False),
                CALCULATORS=json.dumps(calculators.describe(), indent=1),
                CRITERIA=json.dumps(view, indent=1))  # fmt: skip
    raw = llm.call(p, model=MODEL, schema=DRAFT_SCHEMA, effort=EFFORT, max_tokens=64000)
    return json.loads(raw)["criteria"]


def revise(llm, rejected: list[dict]) -> dict:
    p = _prompt("reviser", CALCULATORS=json.dumps(calculators.describe(), indent=1),
                REJECTED=json.dumps(rejected, indent=1))  # fmt: skip
    raw = llm.call(p, model=MODEL, schema=REVISE_SCHEMA, effort=EFFORT, max_tokens=16000)
    return json.loads(raw)
