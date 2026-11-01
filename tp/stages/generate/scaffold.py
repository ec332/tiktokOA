"""Scaffolder (plan G2; design D5–D7): one signed case unit -> one pytest file whose only open
part is the BINDING region. Everything else (case_data, expected values, setup, the locked
assertion block and its hash) is deterministic."""

import hashlib
import re

from tp.core.hashing import sha256

BINDING_START, BINDING_END = "    # >>> BINDING", "    # <<< BINDING"
LOCKED_START, LOCKED_END = "    # >>> LOCKED sha256=", "    # <<< LOCKED"
PLACEHOLDER = "    pass  # the binding goes here"
REGISTRY = ("user", "superuser", "item", "inactive_user")  # env factories (cases design D6)
ABSENT = "<absent>"  # platform_rt.ABSENT
LENGTH = "_length_chars"
STATUS = re.compile(r"[1-5](\d\d|xx)")


class ScaffoldError(Exception):
    pass


# ---- case_data (D6) -------------------------------------------------------------------


def _resolve(openapi: dict, node: dict) -> dict:
    for _ in range(10):  # FastAPI $ref chains are short
        ref = node.get("$ref")
        if not ref:
            return node
        node = openapi
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
    return node


def route_fields(openapi: dict, routes: list[str]) -> dict[str, dict]:
    """field -> its JSON schema, over the query params and JSON body of every route."""
    fields: dict[str, dict] = {}
    for route in routes:
        method, _, path = route.partition(" ")
        op = openapi.get("paths", {}).get(path, {}).get(method.lower())
        if not op:
            continue
        for p in op.get("parameters", []):
            if p["in"] == "query":
                fields.setdefault(p["name"], p.get("schema", {}))
        body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
        if "schema" in body:
            for name, schema in _resolve(openapi, body["schema"]).get("properties", {}).items():
                fields.setdefault(name, schema)
    return fields


def _types(schema: dict) -> set[str]:
    return (
        {schema["type"]} if "type" in schema else {s.get("type") for s in schema.get("anyOf", [])}
    )


def _text(n: int, seed: str) -> str:
    """A string of exactly n characters, the same for the same case id."""
    unit = hashlib.sha256(seed.encode()).hexdigest()
    return (unit * (n // len(unit) + 1))[:n]


def materialise(case_id: str, data: dict, fields: dict[str, dict]) -> tuple[dict, dict]:
    """Abstract case data -> (case_data the request must carry, hints for the binding).
    Only query/body fields of the case's routes become case_data; everything else is a hint."""
    case_data, hints = {}, {}
    for key, raw in data.items():
        name = key.removesuffix(LENGTH)
        if name not in fields or (key.endswith(LENGTH) and not str(raw).lstrip("-").isdigit()):
            hints[key] = raw
            continue
        text = str(raw).strip()
        types = _types(fields[name])
        if key.endswith(LENGTH):
            case_data[name] = _text(int(text), f"{case_id}/{name}")
        elif text.lower() == "null":
            case_data[name] = None
        elif text.lower() == "absent":
            case_data[name] = ABSENT
        elif "integer" in types and re.fullmatch(r"-?\d+", text):
            case_data[name] = int(text)
        elif "boolean" in types and text.lower() in ("true", "false"):
            case_data[name] = text.lower() == "true"
        elif "string" in types:
            case_data[name] = text
        else:
            hints[key] = raw  # a value the schema type can't hold: the binding decides
    return case_data, hints


# ---- expected values (D7) ---------------------------------------------------------------


def expected_of(exp: dict) -> dict:
    """A signed expected value -> what the locked block asserts. Calculator values that are
    objects become one observable per key; a list becomes one observable named after it."""
    value, oracle = exp["value"], exp["oracle"]
    if oracle == "literal" or (isinstance(value, str) and STATUS.fullmatch(value.strip())):
        return {"status": value.strip()}  # e.g. boundary_expect computes a status class
    name = oracle.removeprefix("calculator:").removeprefix("property:")
    if isinstance(value, dict):
        return {"observe": dict(value)}
    return {"observe": {name: value}}


def params_of(unit: dict, fields: dict) -> list[dict]:
    """One entry per parametrized case id: {id, case_data, hints, expected}."""
    if "points" in unit:
        field = unit["boundary"]["field"]
        items = [(p["id"], {field: p["value"]}, {}, p["expected"]) for p in unit["points"]]
    elif "rows" in unit:
        items = [(r["id"], {}, dict(r["when"]), r["expected"]) for r in unit["rows"]]
    else:
        items = [(unit["id"], dict(unit.get("data") or {}), {}, unit["expected"])]
    out = []
    for cid, data, when, exp in items:
        case_data, hints = materialise(cid, data, fields)
        out.append({"id": cid, "case_data": case_data, "hints": when | hints,
                    "expected": expected_of(exp)})  # fmt: skip
    return out


# ---- the request under test (review gaps 1 + 2) ------------------------------------------

VERBS = {"view": {"GET"}, "read": {"GET"}, "get": {"GET"}, "list": {"GET"},
         "change": {"PUT", "PATCH"}, "update": {"PUT", "PATCH"}, "edit": {"PUT", "PATCH"},
         "put": {"PUT"}, "patch": {"PATCH"}, "delete": {"DELETE"}, "remove": {"DELETE"},
         "create": {"POST"}, "post": {"POST"}}  # fmt: skip
OPERATION_KEYS = {"operation", "action", "http_method", "method"}
CALLER_KEYS = {"caller", "actor", "user"}


def request_of(hints: dict, routes: list[str], preconditions: list[str] = ()) -> dict:
    """What the request under test must be, from the hints: which of the case's routes (an
    operation hint narrows the method) and whether it carries a token (`anonymous` -> none).
    shortcut: word lists over the cases' hint vocabulary; other scenario hints stay trusted to
    the binding, and the gate's mutants/seeded variants are what catch a binding ignoring them."""
    methods, auth = set(), None
    for k, v in hints.items():
        value = str(v).strip().lower()
        if k.lower() in OPERATION_KEYS:
            for w in re.findall(r"[a-z]+", value):
                methods |= VERBS.get(w, set())
        if value == "anonymous":
            auth = False
        elif k.lower() in CALLER_KEYS and auth is None:
            auth = True
    if auth is None and {"user", "superuser"} & set(preconditions):
        auth = True  # a user precondition is the caller unless a hint says anonymous (G4 run)
    picked = [r for r in routes if r.partition(" ")[0].upper() in methods]
    return {"routes": picked or list(routes), "auth": auth}


# ---- the file -----------------------------------------------------------------------------


def lock_hash(locked: str, expected: dict, inputs: dict | None) -> str:
    """Covers the locked block, the expected values and (guards v2) the inputs: CASE_DATA,
    HINTS and REQUEST (design D9; HINTS + REQUEST from review gap 3)."""
    return sha256({"locked": locked, "expected": expected, "inputs": inputs})


UNDER_TEST = "api.under_test(CASE_DATA[case_id], REQUEST[case_id])"
INPUTS = ("CASE_DATA", "HINTS", "REQUEST")


def locked_block(kind: str, observables: list[str]) -> str:
    if kind == "status":
        return (f'    expect.status({UNDER_TEST}, EXPECTED[case_id]["status"],'
                ' id=f"{case_id}/status")')  # fmt: skip
    lines = [f"    {UNDER_TEST}"]
    lines += [f'    expect.equals({o!r}, EXPECTED[case_id]["observe"][{o!r}], id=f"{{case_id}}/{o}")'
              for o in observables]  # fmt: skip
    return "\n".join(lines)


def setup_lines(preconditions: list[str]) -> list[str]:
    if unknown := [p for p in preconditions if p not in REGISTRY]:
        raise ScaffoldError(f"preconditions not in the fixture registry: {unknown}")
    lines, seen = [], set()
    for p in preconditions:
        if p in seen:
            continue
        seen.add(p)
        if p == "item":
            owner = "user" if "user" in preconditions else ""
            lines.append(f"    item = make.item({'owner=user' if owner else ''})")
        else:
            lines.append(f"    {p} = make.{p}()")
    return lines


def _literal(name: str, value: dict) -> str:
    import pprint

    return f"{name} = {pprint.pformat(value, sort_dicts=True, width=96)}"  # literal_eval-able


def scaffold(unit: dict, routes: list[str], openapi: dict, guards: str = "v2") -> dict:
    """-> {file, source (with the binding placeholder), lock_hash, params, observables}."""
    params = params_of(unit, route_fields(openapi, routes))
    kinds = {"status" if "status" in p["expected"] else "observe" for p in params}
    if len(kinds) != 1:
        raise ScaffoldError(f"{unit['id']}: mixed expected kinds {sorted(kinds)}")
    kind = kinds.pop()
    observables = sorted({k for p in params for k in p["expected"].get("observe", {})})
    case_data = {p["id"]: p["case_data"] for p in params}
    hints = {p["id"]: p["hints"] for p in params}
    pre = unit.get("preconditions", [])
    request = {p["id"]: request_of(p["hints"], routes, pre) for p in params}
    expected = {p["id"]: p["expected"] for p in params}
    locked = locked_block(kind, observables)
    inputs = {"CASE_DATA": case_data, "HINTS": hints, "REQUEST": request}
    digest = lock_hash(locked, expected, inputs if guards == "v2" else None)
    fn = "test_" + re.sub(r"\W+", "_", unit["id"].lower()).strip("_")
    doc = [f'"""{unit["id"]} ({unit["technique"]}, {unit["ac"]}): {unit["purpose"]}',
           "", f"Routes: {', '.join(routes)}", "Steps:"]  # fmt: skip
    doc += [f"- {s}" for s in unit.get("steps", [])]
    doc += ["", 'Generated by tp generate: only the BINDING region is written by the model."""']
    src = "\n".join([
        *doc, "", "import pytest", "", "from platform_rt import ABSENT, api, expect, observe", "",
        f"UNIT = {unit['id']!r}", _literal("CASE_DATA", case_data), _literal("HINTS", hints),
        _literal("REQUEST", request), _literal("EXPECTED", expected), "", "",
        "@pytest.mark.tp_case(unit=UNIT)",
        "@pytest.mark.parametrize(\"case_id\", list(CASE_DATA))",
        f"def {fn}(make, case_id):",
        "    data, hints = CASE_DATA[case_id], HINTS[case_id]",
        *setup_lines(unit.get("preconditions", [])),
        BINDING_START, PLACEHOLDER, BINDING_END,
        f"{LOCKED_START}{digest}", locked, LOCKED_END, "",
    ])  # fmt: skip
    return {"file": f"{fn}.py", "source": src, "lock_hash": digest, "params": params,
            "request": request,
            "observables": observables, "function": fn}  # fmt: skip


def with_binding(source: str, binding: str) -> str:
    """Put a binding (4-space indented statements) into the scaffold's BINDING region."""
    head, rest = source.split(BINDING_START + "\n", 1)
    _, tail = rest.split(BINDING_END, 1)
    body = "\n".join("    " + ln if ln.strip() else "" for ln in _dedent(binding).splitlines())
    return f"{head}{BINDING_START}\n{body or PLACEHOLDER}\n{BINDING_END}{tail}"


def binding_of(source: str) -> str:
    _, rest = source.split(BINDING_START + "\n", 1)
    return _dedent(rest.split(BINDING_END, 1)[0])


def _dedent(text: str) -> str:
    import textwrap

    return textwrap.dedent(text).strip("\n")
