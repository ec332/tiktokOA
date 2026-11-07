"""The spec stage's index for a target without source (blackbox D14): one pseudo-file per
operation (`openapi/<path>/<METHOD>.yaml`) plus the component schemas they reference, so the
investigator cites `file:line` here exactly as it cites source for a code index."""

import json
import re

import yaml

from tp.stages.spec.code_index import route_key

METHODS = {"get", "post", "put", "patch", "delete"}
REF = re.compile(r"#/components/schemas/([\w.\-]+)")
COMPONENTS = "openapi/components.yaml"


def build(openapi: dict, routes: list[str] | None = None) -> tuple[dict, dict[str, str]]:
    """-> (index shaped like code_index.build's, files). `routes` narrows to those operations."""
    keys = {route_key(*r.split(" ", 1)) for r in routes or [] if " " in r}
    files, out, refs = {}, [], set()
    for path, item in sorted(openapi.get("paths", {}).items()):
        for method, op in sorted(item.items()):
            if method not in METHODS or (keys and route_key(method, path) not in keys):
                continue
            name = f"openapi{path.rstrip('/') or ''}/{method.upper()}.yaml"
            files[name] = yaml.safe_dump({f"{method.upper()} {path}": op}, sort_keys=False)
            refs |= set(REF.findall(json.dumps(op)))
            out.append({"method": method.upper(), "path": path, "handler": op.get("operationId"),
                        "file": name, "line": 1, "end_line": len(files[name].splitlines()),
                        "params": "", "dependencies": None, "mounted_if": None})  # fmt: skip
    schemas = openapi.get("components", {}).get("schemas", {})
    while new := {r for n in refs for r in REF.findall(json.dumps(schemas.get(n, {})))} - refs:
        refs |= new
    comp = {"schemas": {n: schemas[n] for n in sorted(refs) if n in schemas}}
    if sec := openapi.get("components", {}).get("securitySchemes"):
        comp["securitySchemes"] = sec
    if comp["schemas"] or "securitySchemes" in comp:
        doc = {"components": comp} | ({"security": openapi["security"]}
                                      if "security" in openapi else {})  # fmt: skip
        files[COMPONENTS] = yaml.safe_dump(doc, sort_keys=False)
    out.sort(key=lambda x: (x["path"], x["method"]))
    index = {"routes": out, "files": {p: len(t.splitlines()) for p, t in files.items()},
             "unmounted": []}  # fmt: skip
    return index, files
