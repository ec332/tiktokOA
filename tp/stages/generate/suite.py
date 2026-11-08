"""Community suites as BOUND entries (C4): `checks.value.suites` becomes one entry per suite file,
`kind: "suite"`, labelled pre-generated (DR-7). P5 builds this (docs/iteration3/P5-hurl-runner.md);
until then there are none."""


def entries(ctx, opts: dict) -> list[dict]:
    return []
