"""Spec-shape helpers that discovery calls (C6), filled by P4 (docs/status/domains/001 D12, D13)."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml

PACKS = Path(__file__).parents[3] / "packs"

PAGE_OFFSET = {"offset", "limit"}  # an offset and a page length, not a page number and a size


def base(spec: dict, fallback: str) -> str:
    """The base path every operation sits under: the path of `servers[0].url` (OpenAPI 3), else
    Swagger's `basePath`, else `fallback` (the paths' longest common segment prefix). Only the
    path is kept: a server's host is never used (U1). A templated URL, or a root one (`/`, which
    says nothing about where the operations sit), falls back."""
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", ""))
        path = "" if "{" in url else urlsplit(url).path
    else:
        path = spec.get("basePath") if isinstance(spec.get("basePath"), str) else ""
    return "/" + path.strip("/") if path.strip("/") else fallback


def paging_filter(params: list[str]) -> list[str]:
    """The integer query parameters that may be tried as page-style paging (page and size):
    `offset` and `limit` are dropped (research F8: they would verify as page and size)."""
    return [p for p in params if p.lower() not in PAGE_OFFSET]


def with_base(spec: dict) -> dict:
    """The spec with `base(spec)` in front of every path, when its paths don't carry it already
    (RealWorld: `servers` /api, paths /articles). Every consumer treats the base as a prefix of
    the paths (`discovery.common_base`), and requests send paths as they are (domains/002 R2)."""
    b = base(spec, "")
    if not b or all(p == b or p.startswith(b + "/") for p in spec.get("paths") or {}):
        return spec
    return {k: v for k, v in spec.items() if k != "servers"} | {
        "paths": {b + p: v for p, v in spec["paths"].items()}}  # fmt: skip


def pack_spec(name: str) -> tuple[str, dict]:
    """A domain pack's vendored spec (C9), for an implementation with none of its own
    (domains/002 R3): -> (its path under packs/, the spec with its base applied)."""
    pack = yaml.safe_load((PACKS / name / "pack.yaml").read_text())
    rel = f"{name}/{pack['spec']['path']}"
    return rel, with_base(yaml.safe_load((PACKS / rel).read_text()))
