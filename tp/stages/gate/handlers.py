"""Handler mutants (plan K8; gate design 003 D15-D16). mutmut 3 skips decorated functions, so
route handlers get their own mutator: stdlib `ast`, on the host, no runner-image change.

Each route handler whose body holds a signed criterion's code_refs line is rewritten into an env
switch over its mutated bodies; the file is mounted over the original like mutmut's. In stats
mode the switch calls mutmut's `record_trampoline_hit`, which platform_rt redirects to the
test's `reached`, so the gate's stats -> link -> matrix path takes these mutants unchanged."""

import ast
import copy
import difflib
import subprocess
from pathlib import Path

from tp.stages.gate import mutants as mutmut_src

SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE,
        ast.LtE: ast.Gt, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn,
        ast.NotIn: ast.In}  # fmt: skip
VERBS = {"get", "post", "put", "patch", "delete"}


def _is_flag(node: ast.AST) -> bool:
    """`settings.BUG_*` somewhere in node: our flags patch, never product code to mutate."""
    return any(isinstance(n, ast.Attribute) and n.attr.startswith("BUG_") for n in ast.walk(node))


def _points(body: list[ast.stmt]) -> list[ast.AST]:
    """Mutation points in source order. The body of a pure flag gate (`if settings.<FLAG>:`) is
    skipped: with the flag off it never runs, so its mutants would all be equivalent."""
    out: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and _is_flag(node.test)
        ):
            return
        if isinstance(node, ast.If) and not _is_flag(node.test):
            out.append(node)  # negate the condition
        elif isinstance(node, ast.BoolOp | ast.Compare) and not _is_flag(node):
            if isinstance(node, ast.BoolOp) or type(node.ops[0]) in SWAP:
                out.append(node)
        elif (isinstance(node, ast.Constant) and type(node.value) is int
              and not isinstance(node.value, bool)):  # fmt: skip
            out.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in body:
        visit(stmt)
    return out


def _mutate(node: ast.AST) -> str:
    """Change node in place; returns a one-line diff."""
    before = ast.unparse(node.test if isinstance(node, ast.If) else node)
    if isinstance(node, ast.If):
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        after = ast.unparse(node.test)
    else:
        if isinstance(node, ast.BoolOp):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        elif isinstance(node, ast.Compare):
            node.ops[0] = SWAP[type(node.ops[0])]()
        else:
            node.value += 1
        after = ast.unparse(node)
    return f"-{before}\n+{after}"


def _is_handler(fn: ast.AST) -> bool:
    return isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in VERBS
        for d in fn.decorator_list
    )


def _switch(fn: ast.FunctionDef, function: str, bodies: list[tuple[str, list]]) -> None:
    doc = fn.body[:1] if ast.get_docstring(fn) is not None else []
    orig = fn.body[len(doc) :]
    chain: list[ast.stmt] = orig
    for name, body in reversed(bodies):
        test = ast.parse(f"_tp_m == {name!r}", mode="eval").body
        chain = [ast.If(test=test, body=body, orelse=chain)]
    head = ast.parse(
        "_tp_m = _tp_os.environ.get('MUTANT_UNDER_TEST', '')\n"
        "if _tp_m == 'stats':\n"
        "    from mutmut.mutation import trampoline as _tp_t\n"
        f"    _tp_t.record_trampoline_hit({function!r})\n"
    ).body
    fn.body = doc + head + chain


def line_map(old: str, new: str) -> dict[int, int]:
    """1-based line numbers of old -> new, for lines difflib matches."""
    sm = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    return {a + i + 1: b + i + 1 for a, b, n in sm.get_matching_blocks() for i in range(n)}


def _upstream(back: dict[int, int], line: int) -> int | None:
    """A patched line -> upstream; a line the patch rewrote takes the nearest matched one above."""
    return back.get(line) or next((back[k] for k in range(line, 0, -1) if k in back), None)


def generate(src: Path, ref: str, refs: list[tuple[str, int, str]], out: Path,
             exclude: set[str] = frozenset()) -> tuple[dict[str, Path], list[dict]]:  # fmt: skip
    """-> ({container path: mutated file}, mutants in the gate's shape). src: the patched SUT
    tree; spec code_refs are upstream line numbers (at `ref`), so lines are mapped both ways and
    each mutant's `line` is upstream, like the refs. exclude: names left out of the files and
    the list (sealed blind mutants); numbering ignores it, so it stays stable."""
    files, found = {}, []
    by_file: dict[str, set[int]] = {}
    for f, line, _ in refs:
        by_file.setdefault(f, set()).add(line)
    for repo_path, up_lines in sorted(by_file.items()):
        if not (src / repo_path).is_file():
            continue  # a ref to a file that isn't in this tree can't hold a handler
        patched = (src / repo_path).read_text()
        upstream = subprocess.run(["git", "-C", str(src), "show", f"{ref}:{repo_path}"],
                                  check=True, capture_output=True, text=True).stdout  # fmt: skip
        fwd = line_map(upstream, patched)
        back = {v: k for k, v in fwd.items()}
        lines = {fwd[ln] for ln in up_lines if ln in fwd}
        tree = ast.parse(patched)
        module = mutmut_src.module_of(mutmut_src.rel(repo_path))
        touched = False
        for fn in tree.body:
            if not _is_handler(fn) or not any(fn.lineno <= ln <= fn.end_lineno for ln in lines):
                continue
            function = f"{module}.x_{fn.name}"
            bodies = []
            for i, point in enumerate(_points(fn.body), 1):
                name = f"{module}.x_{fn.name}__ast_{i}"
                body = copy.deepcopy(fn.body)
                diff = _mutate(_points(body)[i - 1])  # same traversal order in the copy
                if name in exclude:
                    continue
                bodies.append((name, body[1:] if ast.get_docstring(fn) is not None else body))
                found.append({"name": name, "module": module, "function": function,
                              "file": repo_path, "line": _upstream(back, point.lineno), "diff": diff})  # fmt: skip
            if bodies:
                _switch(fn, function, bodies)
                touched = True
        if touched:
            at = next(
                i
                for i, n in enumerate(tree.body)  # after any `from __future__`
                if not (isinstance(n, ast.ImportFrom) and n.module == "__future__")
            )
            tree.body.insert(at, ast.Import(names=[ast.alias(name="os", asname="_tp_os")]))
            path = out / mutmut_src.rel(repo_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
            files[mutmut_src.image_root() + mutmut_src.rel(repo_path)] = path
    return files, sorted(found, key=lambda m: (m["file"], m["line"] or 0, m["name"]))


# ---- field-constraint mutants (M8 gate D21) -----------------------------------------------
# A feature that lives in model fields (`price: Decimal = Field(ge=0, decimal_places=2)`) has
# no function body for mutmut or the handler mutator. Each constraint keyword on a line the
# branch changed becomes `**({mutated} if MUTANT_UNDER_TEST == name else {original})`. The class
# body runs at import, and the gate's matrix runs one interpreter per mutant, so the switch
# holds for the whole job. No function is "reached", so these mutants link to every test
# that passed in stats mode (function "*").

SWAP_KW = {"ge": "gt", "gt": "ge", "le": "lt", "lt": "le"}
SIZE_KW = {"max_length", "min_length", "max_digits", "decimal_places"}


def _num(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) in (int, float)


def _field_variants(kw: ast.keyword) -> list[tuple[str, ast.AST]]:
    """[(new keyword, new value)] for one constraint keyword."""
    if kw.arg in SWAP_KW:
        out = [(SWAP_KW[kw.arg], kw.value)]
        if _num(kw.value):
            out.append((kw.arg, ast.Constant(kw.value.value + 1)))
        return out
    if kw.arg in SIZE_KW and _num(kw.value):
        return [
            (kw.arg, ast.Constant(kw.value.value + d)) for d in (1, -1) if kw.value.value + d >= 0
        ]
    return []


def fields(src: Path, changed: dict[str, list[int]], out: Path, skip_files: set[str],
           exclude: set[str] = frozenset()) -> tuple[dict[str, Path], list[dict]]:  # fmt: skip
    """-> ({container path: mutated file}, mutants) for `Field(...)` keywords on changed lines."""
    files, found = {}, []
    for repo_path, lines in sorted(changed.items()):
        if repo_path in skip_files or not (src / repo_path).is_file():
            continue
        tree = ast.parse((src / repo_path).read_text())
        module = mutmut_src.module_of(mutmut_src.rel(repo_path))
        want, n = set(lines), 0
        for call in [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                     and getattr(c.func, "id", getattr(c.func, "attr", None)) == "Field"
                     and c.lineno in want]:  # fmt: skip
            new_kws = []
            for kw in call.keywords:
                variants = _field_variants(kw) if kw.arg else []
                chain: ast.AST = ast.Dict([ast.Constant(kw.arg)], [kw.value]) if variants else None
                named = []
                for arg, value in variants:
                    n += 1
                    named.append((f"{module}.field__{n}", arg, value))
                for name, arg, value in reversed(named):
                    if name in exclude:
                        continue
                    before = f"{kw.arg}={ast.unparse(kw.value)}"
                    found.append({"name": name, "module": module, "function": "*",
                                  "file": repo_path, "line": call.lineno, "n": n,
                                  "diff": f"-{before}\n+{arg}={ast.unparse(value)}"})  # fmt: skip
                    test = ast.parse(f"_tp_os.environ.get('MUTANT_UNDER_TEST', '') == {name!r}",
                                     mode="eval").body  # fmt: skip
                    chain = ast.IfExp(test, ast.Dict([ast.Constant(arg)], [value]), chain)
                new_kws.append(ast.keyword(arg=None, value=chain) if variants else kw)
            call.keywords = new_kws
        if any(m["file"] == repo_path for m in found):
            tree.body.insert(0, ast.Import(names=[ast.alias(name="os", asname="_tp_os")]))
            at = out / mutmut_src.rel(repo_path)
            at.parent.mkdir(parents=True, exist_ok=True)
            at.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
            files[mutmut_src.image_root() + mutmut_src.rel(repo_path)] = at
    order = lambda m: (m["file"], m["line"], int(m["name"].rsplit("__", 1)[1]))
    return files, [{k: v for k, v in m.items() if k != "n"} for m in sorted(found, key=order)]
