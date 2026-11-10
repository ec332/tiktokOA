"""Generic check families (checks/001 D1): fixed platform code, no LLM."""

TP_FOCUS = []  # sampled first (checks/002)

from platform_rt.families import pytest_generate_tests, test_generic  # noqa: E402, F401
