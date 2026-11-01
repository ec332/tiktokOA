"""BindingWriter + repair (plan G4–G6; design D8). The model writes only the BINDING region."""

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
MODEL, EFFORT = "claude-haiku-5-5", "low"  # smallest model first (memory: minimal LLM cost)
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["binding", "cannot_bind"],
          "properties": {"binding": {"type": "string"}, "cannot_bind": {"type": "string"}}}  # fmt: skip
# What each calculator observable means (calculators live in the cases stage; names only here).
OBSERVABLES = {
    "count": "the total number of items the list response reports (its `count` field)",
    "data_len": "how many items the returned page contains (length of its `data` list)",
    "newest_first": "for the items you created, their creation positions (1 = created first) "
                    "in the order the list response returns them",
}  # fmt: skip
INSTRUCTIONS = {
    "v1": "The test below failed. Rewrite the binding so that the test passes.",
    "v2": ("The test below failed because of the binding (not because of the product). Fix the "
           "binding. You must still send `data` exactly; the expected outcome is fixed and is "
           "not your concern."),
}  # fmt: skip


def version_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(HERE.glob("prompts/*.md")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    h.update(f"{MODEL}|{EFFORT}|{json.dumps(OBSERVABLES, sort_keys=True)}".encode())
    return h.hexdigest()[:8]


def _prompt(name: str, **fields) -> str:
    text = (HERE / "prompts" / f"{name}.md").read_text()
    for k, v in fields.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def excerpt(openapi: dict, routes: list[str]) -> str:
    """The routes' operations plus the component schemas they reference."""
    ops, refs = {}, set()
    for route in routes:
        method, _, path = route.partition(" ")
        op = openapi.get("paths", {}).get(path, {}).get(method.lower())
        if op:
            ops[route] = {k: op[k] for k in ("parameters", "requestBody", "responses") if k in op}
            refs |= set(re.findall(r"#/components/schemas/(\w+)", json.dumps(op)))
    schemas = openapi.get("components", {}).get("schemas", {})
    for _ in range(3):  # schemas referencing schemas
        refs |= {r for n in list(refs) for r in re.findall(r"#/components/schemas/(\w+)",
                                                           json.dumps(schemas.get(n, {})))}  # fmt: skip
    return json.dumps({"operations": ops, "schemas": {n: schemas[n] for n in sorted(refs)
                                                     if n in schemas}}, indent=1)  # fmt: skip


def hide_expected(source: str) -> str:
    return re.sub(r"^EXPECTED = .*?(?=\n\n)", "EXPECTED = {...}  # hidden from the binding",
                  source, count=1, flags=re.DOTALL | re.MULTILINE)  # fmt: skip


def task(unit: dict, sc: dict, routes: list[str], openapi: dict, setup: list[str]) -> str:
    view = {k: unit.get(k) for k in ("id", "technique", "purpose", "input_class", "steps",
                                      "preconditions")}  # fmt: skip
    observe = "Not used by this test." if not sc["observables"] else "Observe exactly: " + "; ".join(
        f"`{o}` = {OBSERVABLES.get(o, 'see the case')}" for o in sc["observables"])  # fmt: skip
    return _prompt("binding", SETUP=", ".join(f"`{s}`" for s in setup) or "none",
                   OBSERVE=observe, CASE=json.dumps(view, indent=1),
                   OPENAPI=excerpt(openapi, routes), SOURCE=hide_expected(sc["source"]))  # fmt: skip


def _call(llm, prompt: str) -> dict:
    raw = llm.call(prompt, model=MODEL, schema=SCHEMA, effort=EFFORT, max_tokens=8000)
    out = json.loads(raw)
    return {"binding": out["binding"].strip("\n"), "cannot_bind": out["cannot_bind"].strip()}


def write(llm, task_text: str) -> dict:
    return _call(llm, task_text)


def repair(llm, task_text: str, guards: str, failure: str, history: list[dict]) -> dict:
    hist = "\n\n".join(f"Attempt {h['attempt']} ({h['outcome']}):\n```python\n{h['binding']}\n```"
                       for h in history)  # fmt: skip
    return _call(llm, _prompt("repair", INSTRUCTION=INSTRUCTIONS[guards], FAILURE=failure,
                              HISTORY=hist, TASK=task_text))  # fmt: skip
