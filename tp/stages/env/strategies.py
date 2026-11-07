"""The template's strategies (seams/001 A3): container reach, served spec, override auth and data,
transaction state, in_process mutation, pytest_cases checks.

Each one is a thin facade over the env SERVICE (or anything shaped like it, so stage tests can
pass their fakes). Stages call these through `ctx.strategies`; `ctx.env` stays only until every
call site has moved (the allow-list test reaching zero).
"""

from pathlib import Path
from typing import Any

import yaml

from tp.core import contract, profile, strategies, sut


class ContainerReach:
    def __init__(self, svc: Any):
        self.svc = svc

    def provision(self, variant: str = "reference", expect: str | None = None) -> dict:
        return self.svc.up(variant, expect) if expect else self.svc.up(variant)

    def ensure(self, variant: str = "reference") -> None:
        self.svc.ensure(variant)

    def probe(self, variant: str = "reference") -> list[dict]:
        return self.svc.probe(variant)

    def guard(self, variant: str = "reference") -> None:
        self.svc.guard(variant)

    def teardown(self, variant: str = "reference") -> None:
        self.svc.down(variant)

    def logs(self, variant: str = "reference") -> str:
        return self.svc.logs(variant)

    def image(self, variant: str = "reference") -> str | None:
        return self.svc.backend_image(variant)

    def exec(self, cmd: list[str], **kw) -> str | None:
        return self.svc.runner(cmd, **kw)


class ServedSpec:
    def __init__(self, svc: Any):
        self.svc = svc

    def openapi(self, variant: str = "reference") -> dict:
        return self.svc.openapi(variant)


class OverrideAuth:
    """The template's principals are made by its factories module inside the runner."""

    def __init__(self, svc: Any):
        self.svc = svc

    def principal(self, role: str, variant: str = "reference") -> dict:
        raise NotImplementedError("override_module auth: principals come from the data module")


class OverrideData:
    def __init__(self, svc: Any):
        self.svc = svc

    def make(self, tc_id: str, variant: str = "reference") -> Any:
        return self.svc.factories(variant, tc_id)

    def factories(self) -> list[str]:
        """Precondition names the cases may use (blackbox D15): the profile's."""
        return list(profile.load().data.value["factories"])

    def doc(self) -> dict[str, str]:
        """What the binding writer is told about `make` and `api` (the profile's text)."""
        v = profile.load().data.value
        return {"make": v["make_doc"], "api": v["api_doc"], "imports": []}


class TransactionState:
    def __init__(self, svc: Any):
        self.svc = svc

    def reset(self, variant: str = "reference") -> float:
        return self.svc.reset(variant)


class InProcessMutation:
    def __init__(self, svc: Any):
        self.svc = svc

    def available(self) -> bool:
        return True

    def flags(self) -> list[str]:
        """The planted bugs this run may switch on (empty for an unseeded branch target)."""
        return list(sut.load().get("flags", []))

    def scope(self) -> dict:
        return self.svc.code_scope()

    def harness_files(self) -> dict[str, Path]:
        return self.svc.harness_files()

    def seeded(self) -> dict:
        """Which criteria each planted bug targets (gate design D9): the target's seeded file."""
        return yaml.safe_load(profile.seeded_bytes()) or {}


class PytestCases:
    def __init__(self, svc: Any):
        self.svc = svc

    def run(self, tests: Path, out: Path, variant: str = "reference", **kw) -> dict:
        return self.svc.pytest(tests, out, variant, **kw)


IMPLS = {
    ("reach", "container"): ContainerReach,
    ("spec", "served"): ServedSpec,
    ("auth", "override_module"): OverrideAuth,
    ("data", "override_module"): OverrideData,
    ("state", "transaction"): TransactionState,
    ("mutation", "in_process"): InProcessMutation,
    ("checks", "pytest_cases"): PytestCases,
}


def from_service(svc: Any) -> strategies.StrategySet:
    """The template's StrategySet over `svc` (the env SERVICE, or a test fake shaped like it)."""
    picked = {c: cls(svc) for (c, _), cls in IMPLS.items()}
    return strategies.StrategySet(**picked, safety=strategies.Unrestricted(None))


def register() -> None:
    """Factories read the env SERVICE at resolve time, so a test's monkeypatched service is used.

    `container` reach (and its served spec and pytest checks) has two implementations: the env
    service's in-process harness stack needs `transaction` state (the app imported in the runner);
    any other target runs the generic black-box stack (blackbox D6: the profile decides)."""
    from tp.stages.env import blackbox

    def pick(concern, name, cls):
        def factory(p):
            if p.state.strategy != "transaction" and (concern, name) in blackbox.IMPLS:
                return blackbox.strategy(concern, name, p)
            return cls(contract.SERVICES["env"])

        return factory

    for (concern, name), cls in IMPLS.items():
        strategies.register(concern, name, pick(concern, name, cls))
    for concern, name in [*blackbox.IMPLS, ("auth", "basic"), ("auth", "token_header")]:
        if (concern, name) not in IMPLS:
            strategies.register(
                concern, name, lambda p, c=concern, n=name: blackbox.strategy(c, n, p)
            )
