"""Real stacks (plan 3.2 E5, E7, E8). Brings up and tears down its own projects."""

import pytest

from tp.stages.env.factories import Factories
from tp.stages.env.service import EnvDown, Stack, _http

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def reference():
    stack = Stack("reference")
    spec = stack.up()
    yield stack, spec
    stack.down()


def _strip(entity: dict) -> dict:
    return {k: v for k, v in entity.items() if k not in ("id", "owner_id", "token")}


def test_factories_create_identical_data_on_rerun(reference):  # E5
    stack, _ = reference
    runs = []
    for _ in range(2):
        stack.reset()
        f = Factories(stack, "TC-7")
        owner = f.user()
        runs.append([_strip(owner), _strip(f.inactive_user()), _strip(f.item(owner))])
    assert runs[0] == runs[1]
    assert runs[0][1]["is_active"] is False


def test_factories_differ_per_test_case(reference):
    stack, _ = reference
    stack.reset()
    assert Factories(stack, "TC-1").user()["email"] != Factories(stack, "TC-2").user()["email"]


def test_inactive_user_cannot_log_in_on_reference(reference):
    stack, _ = reference
    stack.reset()
    u = Factories(stack, "TC-inactive").inactive_user()
    status, _ = _http(
        "POST",
        stack.api("/login/access-token"),
        form={"username": u["email"], "password": u["password"]},
    )
    assert status == 400


def test_db_down_mid_batch_gives_zero_verdicts(reference):  # E7
    """The DB stops while check 3 is running. That check and every later one is NOT_RUN(ENV):
    no PASS or FAIL is recorded for anything once the env is down."""
    stack, _ = reference
    stack.reset()
    token = Factories(stack, "TC-batch").superuser()["token"]

    def check(i: int) -> bool:
        if i == 3:
            stack.compose("stop", "db")  # goes down mid-check
        status, _ = _http("GET", stack.api("/items/"), token=token)
        return status == 200

    results = []
    try:
        for i in range(6):
            stack.guard()
            if check(i):
                results.append("PASS")
            else:
                stack.guard()  # a failure is only a verdict if the env is still healthy
                results.append("FAIL")
    except EnvDown:
        results += ["NOT_RUN(ENV)"] * (6 - len(results))
    finally:
        stack.compose("up", "-d", "--wait", "db")

    assert results == ["PASS"] * 3 + ["NOT_RUN(ENV)"] * 3
    assert "FAIL" not in results
    stack.guard()  # the stack recovers once the DB is back


def test_bug_variant_reports_its_flag_and_shares_the_image(reference):  # E8
    _, ref_spec = reference
    stack = Stack("bug:BUG_TITLE_MAX")
    try:
        spec = stack.up()
        flags = next(p for p in spec["probes"] if p["name"] == "flags")
        assert flags["ok"] and "BUG_TITLE_MAX" in flags["raw"]
        assert (
            spec["fingerprint"]["images"]["backend"] == ref_spec["fingerprint"]["images"]["backend"]
        )
        assert spec["spec_hash"] != ref_spec["spec_hash"]
        token = Factories(stack, "TC-title").superuser()["token"]
        status, _ = _http("POST", stack.api("/items/"), token=token, json_body={"title": "t" * 255})
        assert status == 422  # the seeded off-by-one is live in this variant only
    finally:
        stack.down()
