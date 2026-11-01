"""EquivalenceProposer (plan K6; gate design D11): proposes which survivors are equivalent.
A human confirms each proposal (review kind `equivalent_mutant`)."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
MODEL, EFFORT = "claude-haiku-5-5", "low"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["proposals"],
          "properties": {"proposals": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["mutant", "equivalent", "reason"],
              "properties": {"mutant": {"type": "string"}, "equivalent": {"type": "boolean"},
                             "reason": {"type": "string"}}}}}}  # fmt: skip


def version_hash() -> str:
    h = hashlib.sha256((HERE / "prompts/equivalence.md").read_bytes())
    h.update(f"{MODEL}|{EFFORT}".encode())
    h.update((HERE / "seeded.yaml").read_bytes())
    return h.hexdigest()[:8]


def propose(llm, survivors: list[dict]) -> list[dict]:
    if not survivors:
        return []
    view = [{"mutant": s["mutant"], "where": f"{s['file']}:{s['line']}", "diff": s["diff"]}
            for s in survivors]  # fmt: skip
    prompt = (HERE / "prompts/equivalence.md").read_text().replace(
        "{{SURVIVORS}}", json.dumps(view, indent=1))  # fmt: skip
    raw = llm.call(prompt, model=MODEL, schema=SCHEMA, effort=EFFORT, max_tokens=16000)
    known = {s["mutant"] for s in survivors}
    return [p for p in json.loads(raw)["proposals"] if p["mutant"] in known]
