"""G2: the Scaffolder, and a failing + a passing fixture for every static check."""

import pytest

from tp.stages.generate import checks
from tp.stages.generate import scaffold as S

GOOD = 'r = api.get("/api/v1/items/", token=user["token"], params={"skip": data["skip"]})'


def _file(unit, fx, uid="TC-O6-2", guards="v2", binding=GOOD):
    u, routes = unit(uid)
    sc = S.scaffold(u, routes, fx["openapi"], guards=guards)
    meta = {"unit": u["id"], "case_ids": [p["id"] for p in sc["params"]],
            "lock_hash": sc["lock_hash"], "guards": guards}  # fmt: skip
    return S.with_binding(sc["source"], binding), meta, sc


def test_materialise_lengths_types_null_absent_and_hints():
    fields = {"title": {"type": "string", "maxLength": 255}, "skip": {"type": "integer"}}
    data, hints = S.materialise("TC-1", {"title_length_chars": "256", "skip": "-1",
                                         "total": "5"}, fields)  # fmt: skip
    assert len(data["title"]) == 256 and data["skip"] == -1 and hints == {"total": "5"}
    assert S.materialise("TC-1", {"title_length_chars": 256}, fields)[0]["title"] == data["title"]
    assert data["title"] == S.materialise("TC-1", {"title_length_chars": "256"}, fields)[0]["title"]
    assert data["title"] != S.materialise("TC-2", {"title_length_chars": "256"}, fields)[0]["title"]
    assert S.materialise("x", {"title": "null"}, fields)[0] == {"title": None}
    assert S.materialise("x", {"title": "absent"}, fields)[0] == {"title": S.ABSENT}
    assert S.materialise("x", {"skip": "lots"}, fields) == ({}, {"skip": "lots"})


def test_boundary_unit_becomes_one_parametrized_test(unit, fx):
    src, meta, sc = _file(unit, fx, "TC-C11-1")
    assert [len(p["case_data"]["title"]) for p in sc["params"]] == [254, 255, 256]
    assert [p["expected"] for p in sc["params"]] == [{"status": "2xx"}] * 2 + [{"status": "4xx"}]
    assert src.count("def test_") == 1 and checks.run(src, meta) == []


def test_calculator_values_become_observables(unit, fx):
    src, meta, sc = _file(unit, fx, "TC-C13-2", binding=(
        'r = api.get("/api/v1/items/", token=user["token"], params=data)\n'
        'observe("count", r.json()["count"])\nobserve("data_len", len(r.json()["data"]))'))  # fmt: skip
    assert sc["observables"] == ["count", "data_len"]
    assert sc["params"][0]["hints"] == {"total": "5"}
    assert "expect.equals('count'" in src and checks.run(src, meta) == []


def test_scaffold_rejects_unknown_preconditions(unit, fx):
    u, routes = unit("TC-O6-2")
    with pytest.raises(S.ScaffoldError, match="registry"):
        S.scaffold(u | {"preconditions": ["admin"]}, routes, fx["openapi"])


def test_with_binding_round_trips(unit, fx):
    src, _, _ = _file(unit, fx)
    assert S.binding_of(src) == GOOD


BAD = {
    "no_bare_assert": "assert r.status_code == 422",
    "locked_at_top_level": None,  # built below: the locked block moved under an if
    "no_skip_or_early_return": 'pytest.skip("flaky")',
    "no_try": "try:\n    r.json()\nexcept Exception:\n    pass",
    "no_nondeterminism": "x = datetime",
    "only_platform_rt": "import httpx",
    "registry_fixtures_only": "admin = make.admin()",
    "one_test": "def helper():\n    pass",
    "no_eq_override": "class Always:\n    def __eq__(self, other):\n        pass",
    "no_patching": "expect.status = print",
    "binding_never_reads_expected": "want = EXPECTED[case_id]",
    "observe_not_constant": 'observe("count", 5)',
}


@pytest.mark.parametrize("check", sorted(checks.CHECKS))
def test_each_check_fails_its_bad_fixture_and_passes_the_good_one(unit, fx, check):
    good, meta, _sc = _file(unit, fx)
    assert checks.CHECKS[check](checks.parse(good), meta) == []
    if check == "locked_block":
        bad = good.replace("'TC-O6-2.a': {'status': '422'}", "'TC-O6-2.a': {'status': '2xx'}")
    elif check == "trace_ids":
        bad = good.replace("UNIT = 'TC-O6-2'", "UNIT = 'TC-X'")
    elif check == "locked_at_top_level":
        line = next(ln for ln in good.splitlines() if ln.startswith("    expect.status("))
        bad = good.replace(line, "    if case_id:\n    " + line)
    else:
        bad = S.with_binding(good, GOOD + "\n" + BAD[check])
    assert bad != good
    assert checks.CHECKS[check](checks.parse(bad), meta), f"{check} let this through:\n{bad}"


def test_also_caught(unit, fx):
    good, meta, _ = _file(unit, fx)
    for snippet, check in [("return", "no_skip_or_early_return"),
                           ("CASE_DATA[case_id] = {}", "no_patching"),
                           ("CASE_DATA.update({})", "no_patching"),
                           ("make.__class__", "only_platform_rt"),
                           ("eval('1')", "only_platform_rt"),
                           ("with open('x'):\n    pass", "no_try")]:  # fmt: skip
        problems = checks.run(S.with_binding(good, GOOD + "\n" + snippet), meta)
        assert any(p.startswith(check) for p in problems), (snippet, problems)
    assert checks.run("def test_x(:\n", meta)[0].startswith("syntax")


def test_case_data_is_locked_in_v2_only(unit, fx):
    """The failure scenario's weakness: in v1, inputs can be edited without breaking the hash."""
    for guards, caught in (("v2", True), ("v1", False)):
        good, meta, _ = _file(unit, fx, guards=guards)
        bad = good.replace("'TC-O6-2.a': {'skip': -1}", "'TC-O6-2.a': {'skip': 0}")
        assert bool(checks.locked_block(checks.parse(bad), meta)) is caught


def test_request_constraints_come_from_the_hints(unit, fx):  # review gap 2
    _, _, sc = _file(unit, fx, "TC-C8-1")
    req = sc["request"]
    assert req["TC-C8-1.1"] == {"routes": ["GET /api/v1/items/{id}"], "auth": True}
    assert req["TC-C8-1.2"]["routes"] == ["PUT /api/v1/items/{id}"]
    assert req["TC-C8-1.3"]["routes"] == ["DELETE /api/v1/items/{id}"]
    assert req["TC-C8-1.5"] == {"routes": ["GET /api/v1/items/{id}"], "auth": False}
    _, _, sc = _file(unit, fx, "TC-C6-1")
    assert sc["request"]["TC-C6-1.2"] == {"routes": ["POST /api/v1/items/"], "auth": False}
    _, _, sc = _file(unit, fx, "TC-O6-2")  # no hints: every route; the user precondition calls
    assert sc["request"]["TC-O6-2.a"] == {"routes": ["GET /api/v1/items/"], "auth": True}


@pytest.mark.parametrize(("old", "new"), [
    ("'operation': 'view'}", "'operation': 'delete'}"),  # HINTS
    ("'auth': False", "'auth': None"),                   # REQUEST
])  # fmt: skip
def test_hints_and_request_are_locked_in_v2_only(unit, fx, old, new):  # review gap 3
    for guards, caught in (("v2", True), ("v1", False)):
        good, meta, _ = _file(unit, fx, "TC-C8-1", guards=guards)
        bad = good.replace(old, new, 1)
        assert bad != good
        assert bool(checks.locked_block(checks.parse(bad), meta)) is caught


def test_a_user_precondition_makes_the_user_the_caller(unit, fx):  # G4 run: no anonymous default
    _, _, sc = _file(unit, fx, "TC-C11-1")
    assert {r["auth"] for r in sc["request"].values()} == {True}
    _, _, sc = _file(unit, fx, "TC-C8-1")
    assert sc["request"]["TC-C8-1.5"]["auth"] is False  # the hint still wins
