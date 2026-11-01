"""CodeIndex + ReverseTrace (plan Stage 1, P1): routes, handlers and file:line from the AST.

Reads the SUT at its pinned ref with `git show`, never the working tree: the tree carries the
platform's patches (seeded BUG_* sites, the test-info probe), which the spec must not see.
"""

import ast
import fnmatch
import re
import subprocess
from pathlib import Path

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SOURCE_GLOB = "backend/app/**.py"  # shortcut: the template's layout; a second SUT would set this


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def source_files(repo: Path, ref: str, exclude: list[str]) -> dict[str, str]:
    """path -> text at `ref`, for in-scope Python files."""
    paths = _git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()
    keep = [
        p
        for p in paths
        if fnmatch.fnmatch(p, SOURCE_GLOB) and not any(fnmatch.fnmatch(p, x) for x in exclude)
    ]
    return {p: _git(repo, "show", f"{ref}:{p}") for p in sorted(keep)}


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def build(files: dict[str, str], api_base: str) -> dict:
    """{routes: [...], files: {path: line count}, unmounted: [router modules never included]}."""
    routers: dict[str, dict] = {}  # module name -> {var, prefix, file}
    routes: list[dict] = []
    includes: dict[str, str | None] = {}  # module name -> enclosing `if` condition (or None)
    aggregators: set[str] = set()  # modules whose router mounts other routers

    for path, text in files.items():
        tree = ast.parse(text, filename=path)
        module = Path(path).stem
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func).endswith("APIRouter")
                and isinstance(node.targets[0], ast.Name)
            ):
                prefix = _str(_kw(node.value, "prefix")) or ""
                routers[module] = {"var": node.targets[0].id, "prefix": prefix, "file": path}
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
                and isinstance(node.args[0], ast.Attribute)
                and isinstance(node.args[0].value, ast.Name)
            ):
                cond, p = None, parents.get(node)
                while p is not None and cond is None:
                    cond = ast.unparse(p.test) if isinstance(p, ast.If) else None
                    p = parents.get(p)
                includes[node.args[0].value.id] = cond
                aggregators.add(module)

    for path, text in files.items():
        module = Path(path).stem
        if module not in routers:
            continue
        r = routers[module]
        for fn in ast.walk(ast.parse(text, filename=path)):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in fn.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in METHODS
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == r["var"]
                    and dec.args
                    and _str(dec.args[0]) is not None
                ):
                    continue
                deps = _kw(dec, "dependencies")
                routes.append(
                    {
                        "method": dec.func.attr.upper(),
                        "path": api_base.rstrip("/") + r["prefix"] + _str(dec.args[0]),
                        "handler": fn.name,
                        "file": path,
                        "line": dec.lineno,
                        "end_line": fn.end_lineno,
                        "params": ast.unparse(fn.args),
                        "dependencies": ast.unparse(deps) if deps is not None else None,
                        "mounted_if": includes.get(module),
                    }
                )
    routes.sort(key=lambda x: (x["path"], x["method"]))
    return {
        "routes": routes,
        "files": {p: len(t.splitlines()) for p, t in files.items()},
        "unmounted": sorted(m for m in routers if m not in includes and m not in aggregators),
    }


def route_key(method: str, path: str) -> str:
    """`GET /api/v1/items/{id}` -> comparable form: method + path with params as {}."""
    return f"{method.upper()} " + re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")


def reverse_trace(index: dict, criteria: list[dict]) -> list[dict]:
    """Routes no criterion mentions: behaviour with no requirement (routes only, design D6)."""
    mentioned = {
        route_key(*r.split(" ", 1)) for c in criteria for r in c.get("routes", []) if " " in r
    }
    return [r for r in index["routes"] if route_key(r["method"], r["path"]) not in mentioned]


def numbered_source(files: dict[str, str]) -> str:
    """Source with line numbers, so the investigator can cite file:line."""
    out = []
    for path, text in files.items():
        out.append(f"=== {path}")
        out += [f"{i:>4}  {line}" for i, line in enumerate(text.splitlines(), 1)]
    return "\n".join(out)
