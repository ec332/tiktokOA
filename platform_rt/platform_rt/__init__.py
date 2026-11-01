"""Test runtime (plan 1.1): generated tests import only this, so every call and assertion is
recorded. `api` sends requests, `expect` asserts (locked block only), `observe` hands a value
from the binding to the locked block."""

from platform_rt.api import ABSENT, api
from platform_rt.expect import expect, observe

__version__ = "0.5.0"
__all__ = ["ABSENT", "api", "expect", "observe"]
