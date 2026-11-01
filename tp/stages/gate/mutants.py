"""MutantSource (plan K1/K5; gate design D5): mutmut generates trampolined copies of the
`code_scope.mutate` files inside the runner; this module parses them into mutants."""

import ast
import difflib
import re
from pathlib import Path

# shortcut: the SUT image keeps the repo's `backend/` at /app/backend (its Dockerfile); a SUT
# with another layout needs this mapping in sut.yaml.
REPO_PREFIX, IMAGE_ROOT = "backend/", "/app/backend/"
MUTANT = re.compile(r"^x_(?P<fn>\w+?)__mutmut_(?P<n>\d+|orig)$")


def rel(path: str) -> str:
    """A code_scope path (repo-relative) -> its path under the image root."""
    return path.removeprefix(REPO_PREFIX)


def module_of(rel_path: str) -> str:
    return rel_path.removesuffix(".py").replace("/", ".")


def generate(env, scope: list[str], out: Path) -> dict[str, Path]:
    """Run mutmut in the runner; returns {container path: host path} of the mutated files.
    The originals are copied next to them (out/orig) for line mapping."""
    out.mkdir(parents=True, exist_ok=True)
    paths = [rel(p) for p in scope]
    cfg = "[mutmut]\\nsource_paths=\\n" + "".join(f"    {p}\\n" for p in paths)
    copy = " && ".join(f"mkdir -p /work/out/mut/{Path(p).parent} /work/out/orig/{Path(p).parent}"
                       f" && cp mutants/{p} /work/out/mut/{p} && cp {IMAGE_ROOT}{p} /work/out/orig/{p}"
                       for p in paths)  # fmt: skip
    script = (f"set -e; rm -rf /tmp/w; cp -r {IMAGE_ROOT.rstrip('/')} /tmp/w; cd /tmp/w; "
              f"printf '{cfg}' > setup.cfg; "
              "python -W ignore -c 'import mutmut.__main__ as m; print(m.create_mutants(1))'; "
              f"{copy}")  # fmt: skip
    env.runner(["sh", "-c", script], writable={"/work/out": out})
    return {IMAGE_ROOT + p: out / "mut" / p for p in paths}


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def parse(out: Path, scope: list[str]) -> list[dict]:
    """Every mutant: {name, module, function, file (repo path), line (in the original file),
    diff}. name is what MUTANT_UNDER_TEST takes; function is what the stats run reports."""
    mutants = []
    for repo_path in scope:
        p = rel(repo_path)
        mut_src = (out / "mut" / p).read_text()
        orig_src = (out / "orig" / p).read_text()
        mut_fns, orig_fns = _functions(ast.parse(mut_src)), _functions(ast.parse(orig_src))
        module = module_of(p)
        for name, node in mut_fns.items():
            m = MUTANT.match(name)
            if not m or m["n"] == "orig":
                continue
            fn = m["fn"]
            base = mut_fns.get(f"x_{fn}__mutmut_orig")
            body = ast.get_source_segment(mut_src, node).splitlines()
            orig = ast.get_source_segment(mut_src, base).splitlines() if base else []
            body[:1] = [body[0].replace(name, fn)] if body else []
            orig[:1] = [orig[0].replace(f"x_{fn}__mutmut_orig", fn)] if orig else []
            changed = next((i for i, (a, b) in enumerate(zip(orig, body, strict=False)) if a != b),
                           min(len(orig), len(body)))  # fmt: skip
            start = orig_fns[fn].lineno if fn in orig_fns else None
            mutants.append({
                # function: as the trampoline reports it in stats mode (mangled: x_<fn>)
                "name": f"{module}.{name}", "module": module, "function": f"{module}.x_{fn}",
                "file": repo_path, "line": start + changed if start else None,
                "diff": "\n".join(difflib.unified_diff(orig, body, lineterm="", n=0))[:1500],
            })  # fmt: skip
    return sorted(mutants, key=lambda m: (m["file"], m["line"] or 0, m["name"]))
