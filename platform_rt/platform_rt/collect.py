"""Test cases in formats other than pytest code (C3, DR-4). A `.hurl` file or a `.ui.yaml` flow is
a test case of its own: collected here by suffix, it writes the same per-test record as any pytest
test, so the gate (K runs, shuffle), the hash lock, triage and retest apply unchanged.

A collector is `collect(file_path, parent) -> pytest.File`. Its items set `tp_case_id` (the
criterion: `S:<pack>:<file>` for suites, `U:<flow>` for UI flows) and `tp_kind`, fill
`record.current()` in `runtest()`, and raise `expect.ExpectationFailed` for a failed assertion:
code inside platform_rt that raises anything else is classed PLATFORM (origin.classify)."""

import importlib

import pytest

COLLECTORS = {".hurl": "platform_rt.hurl:collect", ".ui.yaml": "platform_rt.ui:collect"}
KINDS = {".hurl": "suite", ".ui.yaml": "ui"}  # C1: the record's `kind` for each format


class MissingRunner(Exception):
    """No collector is installed for a test file's format."""


class _Missing(pytest.File):
    """Collection fails (origin BINDING) when a format has no runner: never a silent pass."""

    def collect(self):
        raise MissingRunner(f"no runner for {self.path.name}: {self.tp_target} is not installed")


def suffix_of(name: str) -> str | None:
    return next((s for s in COLLECTORS if name.endswith(s)), None)


def pytest_collect_file(file_path, parent):
    if not (suffix := suffix_of(file_path.name)):
        return None
    target = COLLECTORS[suffix]
    module, _, fn = target.partition(":")
    try:
        collect = getattr(importlib.import_module(module), fn)
    except ImportError, AttributeError:
        missing = _Missing.from_parent(parent, path=file_path)
        missing.tp_target = target
        return missing
    return collect(file_path, parent)
