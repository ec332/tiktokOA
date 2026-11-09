"""Strategy interfaces, the StrategySet and the resolver (iteration 2, seams/001 A3).

Stages reach the target only through `ctx.strategies`: one implementation per concern, picked by
the resolver from the profile's `value.strategy`. Implementations register here the way stages
register in `contract` (the env stage registers the template's container/transaction ones).

`variant` names a build of the target: `reference`, `bug:FLAG[+FLAG]` (planted bugs, in_process
mutation only) or `fixed:<patch>`. Targets without planted bugs only ever use `reference`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tp.core.profile import CONCERNS, TargetProfile


class Reach(Protocol):  # container | external (stage E)
    def provision(self, variant: str = "reference", expect: str | None = None) -> dict: ...
    def ensure(self, variant: str = "reference") -> None: ...
    def probe(self, variant: str = "reference") -> list[dict]: ...
    def teardown(self, variant: str = "reference") -> None: ...
    def logs(self, variant: str = "reference") -> str: ...
    def image(self, variant: str = "reference") -> str | None: ...  # None: nothing to build
    def exec(self, cmd: list[str], **kw) -> str | None: ...  # None: no exec on this reach


class Spec(Protocol):  # served | repo_file | pack_file | code_routes
    def openapi(self, variant: str = "reference") -> dict: ...


class Auth(Protocol):  # none | basic | token_header | bearer_login | override_module
    def principal(self, role: str, variant: str = "reference") -> dict: ...


class Data(Protocol):  # spec_derived | override_module
    def make(self, tc_id: str, variant: str = "reference") -> Any: ...


class State(Protocol):  # transaction | namespace
    def reset(self, variant: str = "reference") -> float: ...


class Mutation(Protocol):  # in_process | none
    def available(self) -> bool: ...
    def flags(self) -> list[str]: ...
    def scope(self) -> dict: ...  # {mutate, src, changed}


class Safety(Protocol):  # unrestricted | safe_mode (stage E)
    def allow(self, method: str, path: str, target_id: str | None = None) -> bool: ...


class Check(Protocol):  # pytest_cases | schemathesis (stage C)
    def run(self, tests: Path, out: Path, variant: str = "reference", **kw) -> dict: ...


@dataclass(frozen=True)
class StrategySet:
    reach: Reach
    spec: Spec
    auth: Auth
    data: Data
    state: State
    mutation: Mutation
    safety: Safety
    checks: Check


# (concern, strategy name) -> factory(profile) -> implementation
REGISTRY: dict[tuple[str, str], Callable[[TargetProfile], Any]] = {}


def register(concern: str, name: str, factory: Callable[[TargetProfile], Any]) -> None:
    if concern not in CONCERNS:
        raise KeyError(f"unknown concern {concern!r}")
    REGISTRY[(concern, name)] = factory


class ResolveError(Exception):
    pass


def resolve(p: TargetProfile) -> StrategySet:
    """One table: the same profile always gives the same strategies."""
    from tp.core import contract

    contract.load()  # stages register their strategy implementations on import
    picked = {}
    for c in CONCERNS:
        name = p.fields[c].strategy
        if (c, name) not in REGISTRY:
            raise ResolveError(
                f"no {c} strategy {name!r} (have {sorted(n for k, n in REGISTRY if k == c)})"
            )
        picked[c] = REGISTRY[(c, name)](p)
    return StrategySet(**picked)


class Unrestricted:
    """Safety for targets the platform owns: every operation is allowed."""

    def __init__(self, _p: TargetProfile):
        pass

    def allow(self, method: str, path: str, target_id: str | None = None) -> bool:
        return True


register("safety", "unrestricted", Unrestricted)
