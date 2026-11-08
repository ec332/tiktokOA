"""Spec-shape helpers that discovery calls (C6). P4 fills them in (docs/iteration3/P4-domain-pack.md);
until then each returns what discovery did before iteration 3."""


def base(spec: dict, fallback: str) -> str:
    """The base path every operation sits under. P4: the path of `servers[0].url`, or Swagger's
    `basePath`. Until then: `fallback`, the paths' longest common segment prefix."""
    return fallback


def paging_filter(params: list[str]) -> list[str]:
    """The integer query parameters that may be tried as page-style paging (page and size). P4:
    drop `offset` and `limit`, which are not a page number and a size. Until then: all of them."""
    return params
