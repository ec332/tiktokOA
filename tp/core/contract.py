"""The one stage contract, the context a stage gets, and the stage registry."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from tp.core.artifacts import Artifact


@dataclass(frozen=True)
class StageResult:
    status: Literal["succeeded", "awaiting_review", "failed"]
    outputs: dict[str, Any] = field(default_factory=dict)  # artifact type -> body
    labels: list[str] = field(default_factory=list)  # extra labels on every output
    review_items: list[int] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def succeeded(cls, outputs: dict[str, Any], labels: list[str] = ()) -> StageResult:
        return cls("succeeded", outputs=outputs, labels=list(labels))

    @classmethod
    def awaiting_review(cls, items: list[int]) -> StageResult:
        return cls("awaiting_review", review_items=items)

    @classmethod
    def failed(cls, reason: str) -> StageResult:
        return cls("failed", reason=reason)


@dataclass
class StageContext:
    """Exactly four things: LLM gateway, review queue, env service, event emitter."""

    llm: Any
    review: Any
    env: Any  # shortcut: None until Stage 5 exists (M1)
    emit: Any


class Stage(Protocol):
    name: str
    version: str
    consumes: dict[str, int]  # artifact type -> schema version
    produces: dict[str, int]  # artifact type -> schema version (plan: list[str]; see docs)

    def run(self, ctx: StageContext, inputs: dict[str, Artifact]) -> StageResult: ...


REGISTRY: dict[str, Stage] = {}  # real stages, registered from tp/stages/<name>/stage.py


def register(stage: Stage) -> Stage:
    REGISTRY[stage.name] = stage
    return stage


def resolve(name: str, stub: bool = False) -> Stage:
    from tp.core.stubs import STUBS

    if stub and name in STUBS:
        return STUBS[name]
    if name in REGISTRY:
        return REGISTRY[name]
    if name in STUBS:
        return STUBS[name]
    raise KeyError(f"no stage registered: {name}")
