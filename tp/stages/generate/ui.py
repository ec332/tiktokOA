"""UI flows as BOUND entries (C4): `checks.value.ui` becomes one entry per flow, `kind: "ui"`, the
flow inlined and hashed. P7 builds this (docs/iteration3/P7-browser-runner.md); until then there
are none."""


def entries(ctx, opts: dict) -> list[dict]:
    return []
