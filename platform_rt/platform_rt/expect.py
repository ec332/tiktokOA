"""Assertions for the locked block. Each one gets an assertion id and is recorded."""

import re

from platform_rt import record


class ExpectationFailed(AssertionError):
    def __init__(self, assertion_id: str, message: str):
        super().__init__(f"[{assertion_id}] {message}")
        self.assertion_id = assertion_id


class MissingObservation(Exception):
    """The locked block asked for a value the binding never observed (a binding fault)."""


def status_matches(got: int, expected: str | int) -> bool:
    exp = str(expected).strip().lower()
    if re.fullmatch(r"[1-5]xx", exp):
        return got // 100 == int(exp[0])
    return got == int(exp)


def _record(aid: str, ok: bool, expected, actual) -> None:
    record.current().assertions.append(
        {"id": aid, "ok": ok, "expected": expected, "actual": actual}
    )
    if not ok:
        raise ExpectationFailed(aid, f"expected {expected!r}, got {actual!r}")


def grounded(value, body) -> bool:
    """Is a scalar observation found in this response body, as a value or a list length?
    shortcut: lists and dicts (e.g. newest_first positions) are derived by the binding and not
    checked; upgrade by having the Scaffolder compute them when a calculator says how."""
    if isinstance(value, (list, dict)):
        return True
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            if len(node) == value and not isinstance(value, bool):
                return True
            stack += node
        elif isinstance(node, dict):
            stack += node.values()
        elif node == value and type(node) is type(value):
            return True
    return False


def _observed(name: str):
    rec = record.current()
    if name not in rec.observed:
        raise MissingObservation(f"the binding never observed {name!r}")
    value = rec.observed[name]
    if rec.under_test is not None and not grounded(value, rec.under_test["body"]):
        from platform_rt.api import ConformanceError

        raise ConformanceError(  # review gap 4: an observation must come from the response
            f"observed {name}={value!r} is not in the response under test"
        )
    return value


class Expect:
    def status(self, exchange: dict, expected: str | int, *, id: str) -> None:
        _record(id, status_matches(exchange["status"], expected), str(expected),
                exchange["status"])  # fmt: skip

    def equals(self, name: str, expected, *, id: str) -> None:
        actual = _observed(name)
        _record(id, actual == expected, expected, actual)

    def matches(self, name: str, pattern: str, *, id: str) -> None:
        actual = _observed(name)
        _record(id, re.fullmatch(pattern, str(actual)) is not None, pattern, actual)


expect = Expect()


def observe(name: str, value) -> None:
    """Binding → locked block: a value extracted from a response (recorded with the run)."""
    record.current().observed[name] = value
