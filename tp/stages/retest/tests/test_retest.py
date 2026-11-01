"""Retest with a scripted fake env (no Docker) driving the real executor: every verdict."""

import copy
from types import SimpleNamespace as NS

import pytest

from tp.core import artifacts, db
from tp.stages.execute import record
from tp.stages.retest import decide
from tp.stages.retest.stage import RetestStage

DEF, OK, PRE = "test_d.py::test_d[TC-D]", "test_ok.py::test_ok[TC-OK]", "test_p.py::test_p[TC-P]"
FP = {"services": {"db": "s1", "backend": "b-ref"}, "images": {"db": "pg", "backend": "i-ref"},
      "sut": {"name": "x", "ref": "f4b", "patches": {"flags": "p1"}}, "flags": [],
      "seed_hash": "seed", "tools": {"docker": "27"}}  # fmt: skip


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))


def fp(variant: str) -> dict:
    out = copy.deepcopy(FP)
    if variant.startswith("fixed:"):
        out["images"]["backend"], out["services"]["backend"] = "i-fix", "b-fix"
        out["sut"]["patches"][variant[6:]] = "p2"
    if variant.startswith("bug:"):
        out["flags"], out["services"]["backend"] = [variant[4:]], "b-flag"
    return out


class FakeEnv:
    """DEF fails on reference and on bug:X; PRE always fails; OK fails only if `regress`."""

    def __init__(self, regress=False, db_image="pg", dead=False):
        self.regress, self.db_image, self.dead, self.ups = regress, db_image, dead, []

    def up(self, variant):
        self.ups.append(variant)
        f = fp(variant)
        f["images"]["db"] = self.db_image
        return {"spec_hash": "h", "flags": f["flags"], "reproduce": "", "fingerprint": f}

    def probe(self, variant):
        return [{"name": "db", "ok": not (self.dead and variant != "reference"), "raw": ""}]

    def pytest(self, tests, out, variant, mode, env):
        fails = {PRE} | ({DEF} if not variant.startswith("fixed:") else set())
        fails |= {OK} if self.regress and variant != "reference" else set()
        return {"results": [{"nodeid": n, "outcome": "failed" if n in fails else "passed",
                             "origin": "ASSERTION", "assertions": [{"id": n, "ok": n not in fails}]}
                            for n in (DEF, OK, PRE)]}  # fmt: skip

    def logs(self, variant):
        return ""

    def down(self, variant):
        pass


def bound(changed=False):
    src = lambda n: f"def test(): assert {n}\n"
    tests = [{"file": n.split("::")[0], "source": src(n), "lock_hash": "L" + n, "status": "BOUND"}
             for n in (DEF, OK, PRE)]  # fmt: skip
    if changed:
        tests[0]["lock_hash"] = "edited after the fix"
    return {"tests": tests}


def failing_run(before="reference", fingerprint=True):
    """A sealed before-run: DEF and PRE 3/3 failing on `before`."""
    b = artifacts.put("bound_tests", bound())
    folder = db.data_dir() / "runs" / "execute-1"
    root = record.write(
        folder, {"manifest.json": f'{{"bound_tests": "{b.id}"}}', "results.jsonl": ""}
    )
    vd = lambda n, case, v: {"node": n, "case_id": case, "variant": before, "verdict": v,
                             "dist": "3/3" if v != "PASS" else "0/3"}  # fmt: skip
    return {"folder": "runs/execute-1", "root": root,
            "variants": {before: {"fingerprint": fp(before) if fingerprint else None}},
            "verdicts": [vd(DEF, "TC-D", "CONSISTENT_FAIL"), vd(OK, "TC-OK", "PASS"),
                         vd(PRE, "TC-P", "CONSISTENT_FAIL")]}  # fmt: skip


def run(
    env, after, before="reference", changed=False, fingerprint=True, defect=None, cases=("TC-D",)
):
    art = lambda body: NS(body=body, id="sha256:x")
    gate = {"rows": [{"node": n, "case_id": n.split("[")[1][:-1], "status": "ADMITTED"}
                     for n in (DEF, OK, PRE)]}  # fmt: skip
    tri = {"defects": [{"id": f"D-{before}/O-6", "variant": before, "ac": "O-6",
                        "class": "PRODUCT_DEFECT", "cases": list(cases)}]}  # fmt: skip
    inputs = {"run_records": art(failing_run(before, fingerprint)), "triage": art(tri),
              "bound_tests": art(bound(changed)), "gate_report": art(gate),
              "run_options": art({"defect": defect or f"D-{before}/O-6", "after": after})}  # fmt: skip
    ctx = NS(env=env, emit=lambda *a, **kw: None, review=NS(execution_id=2))
    res = RetestStage().run(ctx, inputs)
    assert res.status == "succeeded", res.reason
    return res.outputs["retest"]["retests"][0]


def test_own_patch_fix_is_confirmed_with_pre_existing_failures_kept_apart():  # V3
    r = run(FakeEnv(), "fixed:fix-pagination")
    assert r["verdict"] == "FIXED_CONFIRMED" and r["closed"]
    assert r["fix_kind"] == "own patch on upstream" and r["tests_changed"] == []
    assert r["fail_to_pass"][0] == {"node": DEF, "case_id": "TC-D", "before": "CONSISTENT_FAIL",
                                         "before_dist": "3/3", "after": "PASS", "after_dist": "0/5"}  # fmt: skip
    assert r["pre_existing"] == [{"node": PRE, "case_id": "TC-P", "before": "CONSISTENT_FAIL",
                                  "after": "CONSISTENT_FAIL"}]  # fmt: skip
    assert r["after"]["k"] == 5 and r["env_diff"] == []


def test_a_changed_test_is_refused_before_anything_runs():  # V1
    env = FakeEnv()
    r = run(env, "fixed:fix-pagination", changed=True)
    assert r["verdict"] == "RETEST_INVALID" and not r["closed"]
    assert r["tests_changed"] == ["test_d.py"] and env.ups == []
    assert r["unsure"] == [] and r["pass_to_pass"]["regressions"] == []


def test_seeded_flag_off_but_still_failing_is_not_fixed():  # V2 shape; reference still fails DEF
    r = run(FakeEnv(), "reference", before="bug:BUG_X")
    assert r["fix_kind"] == "flag off (seeded)" and r["verdict"] == "NOT_FIXED"


def test_a_test_that_passed_before_and_fails_after_is_a_regression():
    r = run(FakeEnv(regress=True), "fixed:fix-pagination")
    assert r["verdict"] == "FIXED_WITH_REGRESSION" and r["pass_to_pass"]["regressions"] == [OK]


@pytest.mark.parametrize(
    "env,kw",
    [
        (FakeEnv(db_image="pg-other"), {}),
        (FakeEnv(), {"fingerprint": False}),
        (FakeEnv(dead=True), {}),
    ],
    ids=["db image differs", "no fingerprint", "env died"],
)
def test_anything_but_code_or_flags_changing_is_inconclusive(env, kw):
    assert run(env, "fixed:fix-pagination", **kw)["verdict"] == "INCONCLUSIVE"


def test_no_defect_option_is_a_no_op_in_the_pipeline():
    art = NS(body={}, id="x")
    ctx = NS(env=None, emit=lambda *a, **kw: None)
    res = RetestStage().run(ctx, dict.fromkeys(RetestStage.consumes, art))
    assert res.outputs["retest"] == {"retests": [], "note": "no fix supplied"}


def test_env_diff_allows_only_code_and_flags():
    assert decide.env_diff(fp("reference"), fp("fixed:f")) == []
    assert decide.env_diff(fp("bug:B"), fp("reference")) == []
    removed = fp("fixed:f")
    assert decide.env_diff(removed, fp("reference")) == [
        "sut.patches (a patch changed or was removed)"
    ]
    other = fp("reference") | {"seed_hash": "other"}
    assert decide.env_diff(fp("reference"), other) == ["seed_hash"]
    rebuilt = fp("fixed:f") | {"runner": "sha256:looser-expect"}  # review gap 3
    assert decide.env_diff(fp("reference") | {"runner": "sha256:r1"}, rebuilt) == ["runner"]


@pytest.mark.parametrize(
    "cases", [["TC-NOPE"], ["TC-OK"]], ids=["triage from another run", "passed in the failing run"]
)
def test_a_defect_not_reproduced_in_the_failing_run_proves_nothing(cases):
    env = FakeEnv()
    r = run(env, "fixed:fix-pagination", cases=cases)
    assert r["verdict"] == "INCONCLUSIVE" and not r["reproduced"] and not r["closed"]
    assert env.ups == []
