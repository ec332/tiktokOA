"""Stub stages. Each file is deleted in the commit that creates tp/stages/<name>/ (plan 1.4)."""

from tp.core.contract import StageResult


class Stub:
    version = "0.0.0-stub"

    def __init__(self, name: str, consumes: list[str], produces: str):
        self.name = name
        self.consumes = {t: 1 for t in consumes}
        self.produces = {produces: 1}

    def run(self, ctx, inputs):
        ctx.emit("stub_ran", stage=self.name)
        body = {"stub": self.name, "inputs": {t: a.id for t, a in inputs.items()}}
        return StageResult.succeeded({next(iter(self.produces)): body})


def _load() -> dict[str, Stub]:
    from tp.core.stubs import (
        cases,
        env,
        execute,
        gate,
        generate,
        report,
        retest,
        spec,
        triage,
    )

    mods = [spec, cases, generate, gate, env, execute, triage, report, retest]
    return {m.STAGE.name: m.STAGE for m in mods}


STUBS = _load()
