"""StaticChecks (plan G2): AST rules every generated test must pass before it may run.
Each check is a pure function of the parsed file -> problems ([] = pass)."""

import ast
from dataclasses import dataclass

from tp.stages.generate import scaffold as S

ALLOWED_IMPORTS = {("pytest", None), ("platform_rt", "ABSENT"), ("platform_rt", "api"),
                   ("platform_rt", "expect"), ("platform_rt", "observe")}  # fmt: skip
NONDETERMINISTIC_NAMES = {"random", "time", "datetime", "uuid", "secrets", "os", "sys"}
NONDETERMINISTIC_ATTRS = {"sleep", "now", "utcnow", "today", "uuid4", "random", "randint",
                          "choice", "shuffle", "time_ns", "perf_counter", "monotonic"}  # fmt: skip
DYNAMIC = {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "delattr",
           "globals", "locals", "vars", "breakpoint", "input"}  # fmt: skip
PROTECTED = {"api", "expect", "observe", "platform_rt", "pytest", "CASE_DATA", "EXPECTED",
             "HINTS", "REQUEST", "UNIT", "make", "ABSENT"}  # fmt: skip
MUTATORS = {"update", "pop", "popitem", "clear", "setdefault", "__setitem__", "__delitem__"}
SKIPS = {"skip", "xfail", "importorskip", "skipif", "fail", "exit"}


@dataclass
class Parsed:
    source: str
    tree: ast.Module
    binding: tuple[int, int]  # first, last line (1-based, inclusive) inside the BINDING region
    locked: tuple[int, int]
    locked_text: str
    header_hash: str | None
    markers: dict[str, int]  # marker -> count


def parse(source: str) -> Parsed:
    lines = source.splitlines()
    tree = ast.parse(source)

    def find(prefix: str) -> list[int]:
        return [i + 1 for i, ln in enumerate(lines) if ln.startswith(prefix)]

    markers = {m: len(find(m)) for m in (S.BINDING_START, S.BINDING_END, S.LOCKED_START,
                                          S.LOCKED_END)}  # fmt: skip
    b0, b1 = (find(S.BINDING_START) or [0])[0], (find(S.BINDING_END) or [0])[0]
    l0, l1 = (find(S.LOCKED_START) or [0])[0], (find(S.LOCKED_END) or [0])[0]
    header = lines[l0 - 1].removeprefix(S.LOCKED_START).strip() if l0 else None
    return Parsed(source, tree, (b0 + 1, b1 - 1), (l0 + 1, l1 - 1),
                  "\n".join(lines[l0:l1 - 1]) if l0 and l1 else "", header, markers)  # fmt: skip


def _module_literal(p: Parsed, name: str):
    for node in p.tree.body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [
            name
        ]:
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def _in(node: ast.AST, span: tuple[int, int]) -> bool:
    return span[0] <= getattr(node, "lineno", -1) <= span[1]


def _binding_nodes(p: Parsed) -> list[ast.AST]:
    return [n for n in ast.walk(p.tree) if _in(n, p.binding)]


def _tests(p: Parsed) -> list[ast.FunctionDef]:
    return [n for n in p.tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]


def _root(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


# ---- the checks ------------------------------------------------------------------------


def locked_block(p: Parsed, meta: dict) -> list[str]:
    if any(n != 1 for n in p.markers.values()):
        return [f"region markers must each appear once: {p.markers}"]
    if not p.binding[0] <= p.binding[1] + 1 <= p.locked[0] - 1:
        return ["the BINDING region must come before the LOCKED block"]
    expected = _module_literal(p, "EXPECTED")
    inputs = {n: _module_literal(p, n) for n in S.INPUTS} if meta["guards"] == "v2" else None
    digest = S.lock_hash(p.locked_text, expected, inputs)
    out = []
    if digest != p.header_hash:
        out.append("locked block, expected values or case data changed (hash mismatch)")
    if p.header_hash != meta["lock_hash"]:
        out.append("lock hash differs from the scaffold's")
    return out


def no_bare_assert(p: Parsed, meta: dict) -> list[str]:
    return [f"line {n.lineno}: bare assert" for n in ast.walk(p.tree) if isinstance(n, ast.Assert)]


def locked_at_top_level(p: Parsed, meta: dict) -> list[str]:
    tests = _tests(p)
    if not tests:
        return ["no test function"]
    top = {n.lineno for n in tests[0].body}
    inner = [n for n in ast.walk(tests[0]) if isinstance(n, ast.stmt) and _in(n, p.locked)]
    out = [f"line {n.lineno}: locked statement is nested" for n in inner if n.lineno not in top]
    out += [f"line {n.lineno}: compound statement in the locked block" for n in inner
            if isinstance(n, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.Match))]  # fmt: skip
    return out


def no_skip_or_early_return(p: Parsed, meta: dict) -> list[str]:
    out = []
    for n in ast.walk(p.tree):
        if isinstance(n, ast.Attribute) and n.attr in SKIPS and _root(n) == "pytest":
            out.append(f"line {n.lineno}: pytest.{n.attr}")
        if isinstance(n, (ast.Return, ast.Raise, ast.Yield, ast.YieldFrom)):
            out.append(f"line {n.lineno}: {type(n).__name__.lower()} in the test")
    return out


def no_try(p: Parsed, meta: dict) -> list[str]:
    out = [
        f"line {n.lineno}: try" for n in ast.walk(p.tree) if isinstance(n, (ast.Try, ast.TryStar))
    ]
    out += [
        f"line {n.lineno}: with-statement" for n in _binding_nodes(p) if isinstance(n, ast.With)
    ]
    return out


def no_nondeterminism(p: Parsed, meta: dict) -> list[str]:
    out = []
    for n in ast.walk(p.tree):
        if isinstance(n, ast.Name) and n.id in NONDETERMINISTIC_NAMES:
            out.append(f"line {n.lineno}: {n.id}")
        if isinstance(n, ast.Attribute) and n.attr in NONDETERMINISTIC_ATTRS:
            out.append(f"line {n.lineno}: .{n.attr}")
    return out


def only_platform_rt(p: Parsed, meta: dict) -> list[str]:
    out = []
    for n in ast.walk(p.tree):
        if isinstance(n, ast.Import):
            out += [f"line {n.lineno}: import {a.name}" for a in n.names
                    if (a.name, None) not in ALLOWED_IMPORTS]  # fmt: skip
        elif isinstance(n, ast.ImportFrom):
            out += [f"line {n.lineno}: from {n.module} import {a.name}" for a in n.names
                    if (n.module, a.name) not in ALLOWED_IMPORTS]  # fmt: skip
        elif isinstance(n, ast.Name) and n.id in DYNAMIC:
            out.append(f"line {n.lineno}: {n.id}")
        elif isinstance(n, ast.Attribute) and n.attr.startswith("__") and n.attr != "__name__":
            out.append(f"line {n.lineno}: dunder access .{n.attr}")
    return out


def trace_ids(p: Parsed, meta: dict) -> list[str]:
    want = sorted(meta["case_ids"])
    out = []
    for name in (*S.INPUTS, "EXPECTED"):
        got = _module_literal(p, name)
        if not isinstance(got, dict) or sorted(got) != want:
            out.append(f"{name} keys must be exactly the unit's case ids {want}")
    if _module_literal(p, "UNIT") != meta["unit"]:
        out.append("UNIT must be the case unit id")
    return out


def registry_fixtures_only(p: Parsed, meta: dict) -> list[str]:
    out = []
    for t in _tests(p):
        args = [a.arg for a in t.args.args]
        if args != ["make", "case_id"]:
            out.append(f"test arguments must be (make, case_id), got {args}")
    for n in ast.walk(p.tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "make" and n.attr not in S.REGISTRY):  # fmt: skip
            out.append(f"line {n.lineno}: make.{n.attr} is not a registry fixture")
    return out


def one_test(p: Parsed, meta: dict) -> list[str]:
    defs = [n for n in ast.walk(p.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(defs) != 1 or not _tests(p):
        return [f"exactly one test function and no other functions, got {[d.name for d in defs]}"]
    return []


def no_eq_override(p: Parsed, meta: dict) -> list[str]:
    return [f"line {n.lineno}: class definition" for n in ast.walk(p.tree)
            if isinstance(n, ast.ClassDef)]  # fmt: skip


def no_patching(p: Parsed, meta: dict) -> list[str]:
    """No monkeypatching of platform_rt, the fixtures or the module constants."""
    out = []
    for n in ast.walk(p.tree):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        elif isinstance(n, ast.Delete):
            targets = n.targets
        for t in targets:
            root = _root(t)
            if root in PROTECTED and (isinstance(t, (ast.Attribute, ast.Subscript))
                                      or _in(n, p.binding)):  # fmt: skip
                out.append(f"line {n.lineno}: assigns to {root}")
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            out.append(f"line {n.lineno}: {type(n).__name__.lower()}")
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in MUTATORS and _root(n.func.value) in PROTECTED):  # fmt: skip
            out.append(f"line {n.lineno}: mutates {_root(n.func.value)}")
        if isinstance(n, ast.Name) and n.id == "monkeypatch":
            out.append(f"line {n.lineno}: monkeypatch")
    return out


def binding_never_reads_expected(p: Parsed, meta: dict) -> list[str]:
    return [f"line {n.lineno}: the binding reads EXPECTED" for n in _binding_nodes(p)
            if isinstance(n, ast.Name) and n.id == "EXPECTED"]  # fmt: skip


def observe_not_constant(p: Parsed, meta: dict) -> list[str]:
    out = []
    for n in _binding_nodes(p):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "observe":
            if len(n.args) != 2:
                out.append(f"line {n.lineno}: observe(name, value)")
                continue
            try:
                ast.literal_eval(n.args[1])
                out.append(f"line {n.lineno}: observe() of a constant")
            except ValueError:
                pass
    return out


CHECKS = {f.__name__: f for f in (
    locked_block, no_bare_assert, locked_at_top_level, no_skip_or_early_return, no_try,
    no_nondeterminism, only_platform_rt, trace_ids, registry_fixtures_only, one_test,
    no_eq_override, no_patching, binding_never_reads_expected, observe_not_constant,
)}  # fmt: skip


def run(source: str, meta: dict) -> list[str]:
    """meta: {unit, case_ids, lock_hash, guards}. Returns "check: problem" strings."""
    try:
        p = parse(source)
    except SyntaxError as e:
        return [f"syntax: line {e.lineno}: {e.msg}"]
    return [f"{name}: {msg}" for name, fn in CHECKS.items() for msg in fn(p, meta)]
