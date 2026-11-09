"""Spec-shape helpers that discovery calls (C6), filled by P4 (docs/status/domains/001 D12, D13)."""

from urllib.parse import urlsplit

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
