"""Arm (a), the naive pipeline (design B5-B6): the LLM sees the code, writes whole tests; tests
failing on reference get up to REPAIRS rounds, then whatever still fails is dropped."""

import json
import re
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
MODEL = "claude-haiku-5-5"  # the platform's generate model (memory: minimal LLM cost)
REPAIRS = 3  # generate's repair budget (generate design D8)
TARGET = 60  # about the platform's 59 test cases
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["imports", "tests"],
          "properties": {"imports": {"type": "string"}, "tests": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["name", "source"],
              "properties": {"name": {"type": "string"}, "source": {"type": "string"}}}}}}  # fmt: skip
TEST_FILE = "test_naive.py"
DEF = re.compile(r"^def (test_\w+)\(", re.MULTILINE)


def _call(llm, template: str, **fields) -> dict:
    prompt = (HERE / "prompts" / template).read_text()
    for k, v in fields.items():  # not str.format: the source is full of braces; source is last
        prompt = prompt.replace("{" + k + "}", str(v))
    return json.loads(llm.call(prompt, model=MODEL, schema=SCHEMA, max_tokens=32000))


def _tests(resp: dict) -> dict[str, str]:
    """name -> source, keyed by the def's own name (the JSON name may disagree)."""
    out = {}
    for t in resp["tests"]:
        if m := DEF.search(t["source"]):
            out[m[1]] = t["source"].strip()
    return out


def write(folder: Path, imports: str, tests: dict[str, str]) -> None:
    (folder / TEST_FILE).write_text(
        imports.strip() + "\n\n\n" + "\n\n\n".join(tests.values()) + "\n"
    )


def failures(records: list[dict], names: list[str]) -> dict[str, str]:
    """Failing test function -> error. A collection error fails every test."""
    out = {}
    for r in records:
        err = (r.get("failure") or {}).get("error", "")
        if r["outcome"] == "passed":
            continue
        if "::" not in r["nodeid"]:  # the module failed to collect
            return dict.fromkeys(names, err)
        out.setdefault(r["nodeid"].split("::")[1].split("[")[0], err)
    ran = {r["nodeid"].split("::")[1].split("[")[0] for r in records if "::" in r["nodeid"]}
    return out | {n: "not collected" for n in names if n not in ran}


def naive(llm, source: str, folder: Path, run: Callable[[Path], list[dict]], sample: int) -> dict:
    """folder: the SUT's conftest + helpers; the test file is written into it. run(folder) runs
    it once on reference and returns platform_rt's records."""
    resp = _call(llm, "naive.md", target=TARGET, sample=sample, source=source)
    imports, tests = resp["imports"], _tests(resp)
    generated, repaired, rounds = len(tests), set(), 0
    while True:
        write(folder, imports, tests)
        failing = failures(run(folder), list(tests))
        if not failing or rounds == REPAIRS:
            break
        rounds += 1
        block = "\n\n".join(f"### {n}\nError: {e[-1500:]}\n```python\n{tests[n]}\n```"
                            for n, e in failing.items())  # fmt: skip
        fix = _call(llm, "repair.md", imports=imports, failing=block, source=source)
        imports = fix["imports"].strip() or imports
        for n, src in _tests(fix).items():
            if n in failing:
                tests[n] = src
                repaired.add(n)
    dropped = {n: failing[n][-500:] for n in failing}
    for n in dropped:
        tests.pop(n)
    write(folder, imports, tests)
    return {"sample": sample, "imports": imports,
            "tests": [{"name": n, "source": s} for n, s in tests.items()],
            "generated": generated, "repair_rounds": rounds,
            "repaired": sorted(repaired - set(dropped)), "dropped": dropped}  # fmt: skip
