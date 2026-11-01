"""Per-test recorder: exchanges, observations, assertions. One is active per running test."""

import hashlib
import os
from dataclasses import dataclass, field


@dataclass
class Recorder:
    nodeid: str = "<none>"
    tc: str = "<none>"
    run: str = field(default_factory=lambda: os.environ.get("TP_RUN", "local"))
    exchanges: list[dict] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    observed: dict = field(default_factory=dict)
    under_test: dict | None = None
    case_data: dict | None = None  # what under_test() was asked to match
    steps: int = 0
    reached: set = field(default_factory=set)  # mutated functions called (MUTANT_UNDER_TEST=stats)

    @property
    def trace_id(self) -> str:  # W3C trace-id: 32 hex, stable per run + test
        return hashlib.sha256(f"{self.run}/{self.nodeid}".encode()).hexdigest()[:32]

    def next_step(self) -> int:
        self.steps += 1
        return self.steps


_current = Recorder()


def current() -> Recorder:
    return _current


def start(nodeid: str, tc: str) -> Recorder:
    global _current
    _current = Recorder(nodeid=nodeid, tc=tc)
    return _current
