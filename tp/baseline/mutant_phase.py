"""pytest plugin for the baseline's in-process arms (design B3), loaded with `-p mutant_phase`.
The mutant is off during fixture setup/teardown and on only in the test body, as platform_rt
does per setup request: a kill then means the test itself exposed the mutant."""

import os
from contextlib import contextmanager

import pytest


@contextmanager
def _mutant_off():
    mutant = os.environ.pop("MUTANT_UNDER_TEST", None)
    try:
        yield
    finally:
        if mutant is not None:
            os.environ["MUTANT_UNDER_TEST"] = mutant


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    with _mutant_off():
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    with _mutant_off():
        yield
