"""Execute with a scripted fake env (no Docker): lanes, variants, guards, escalation, sealing."""

import itertools
from types import SimpleNamespace as NS

from tp.core import db
from tp.stages.execute import record
from tp.stages.execute.stage import ExecuteStage

OWN = "test_tc_c8_2.py::test_tc_c8_2[TC-C8-2]"  # BUG_OWNERSHIP_UPDATE's targeted row (gate K4)
RACE = "test_tc_c10_1.py::test_tc_c10_1[TC-C10-1]"  # stands in for a race-sensitive test


class FakeEnv:
    def __init__(self, records, die_after=None, flaky=False):
        self.records, self.die_after, self.flaky = records, die_after, flaky
        self.batches, self.calls, self.down_called = 0, [], []
        self._coin = itertools.cycle([True, False, False])

    def up(self, variant):
        return {"spec_hash": "h-" + variant, "flags": [], "reproduce": f"tp env up {variant}"}

    def probe(self, variant):
        dead = self.die_after is not None and self.batches > self.die_after
        return [{"name": "db", "ok": not dead, "raw": "connection refused" if dead else "ok"}]

    def pytest(self, tests, out, variant, mode, env):
        self.batches += 1
        self.calls.append((variant, env))
        files = {p.name for p in tests.iterdir()}
        out = []
        for r in self.records:
            if r["nodeid"].split("::")[0] not in files:
                continue
            r = dict(r)
            if variant == "bug:BUG_OWNERSHIP_UPDATE" and r["nodeid"] == OWN:
                r |= {"outcome": "failed", "origin": "ASSERTION",
                      "assertions": [{"id": "TC-C8-2/status", "ok": False}]}  # fmt: skip
            if (
                self.flaky
                and variant == "bug:BUG_FLAKE_RACE"
                and r["nodeid"] == RACE
                and next(self._coin)
            ):
                r |= {"outcome": "failed", "origin": "ASSERTION",
                      "assertions": [{"id": "TC-C10-1/status", "ok": False}]}  # fmt: skip
            out.append(r)
        return {"results": out}

    def logs(self, variant):
        return f"INFO:app.trace:GET / 200 x-test-trace=x1.{variant}.a1/TC/1\n"

    def down(self, variant):
        self.down_called.append(variant)


def _run(fx, env, variants, k=3):
    art = lambda body, i: NS(body=body, id=f"sha256:{i}")
    inputs = {"gate_report": art(fx["gate_report"], "g"), "bound_tests": art(fx["bound_tests"], "b"),
              "env_spec": art({"spec_hash": "h-reference"}, "e"),
              "run_options": art({"variants": variants, "k": k}, "o")}  # fmt: skip
    ctx = NS(env=env, emit=lambda *a, **kw: None, review=NS(execution_id=1))
    return ExecuteStage().run(ctx, inputs)


def _by(body, variant):
    return {v["node"]: v for v in body["verdicts"] if v["variant"] == variant}


def test_every_gate_row_runs_and_the_reference_distribution_matches_the_gate(fx):
    res = _run(fx, FakeEnv(fx["records"]), ["reference"])
    body = res.outputs["run_records"]
    ref = _by(body, "reference")
    assert len(ref) == 59  # all statuses, incl. SPEC_GAP and WEAK (design D1)
    fails = {v["case_id"] for v in ref.values() if v["verdict"] == "CONSISTENT_FAIL"}
    assert len(fails) == 13 and "TC-O6-2.a" in fails and "TC-C13-1.a" in fails
    assert {v["dist"] for v in ref.values()} == {"0/3", "3/3"}


def test_trace_run_id_and_shuffle_on_the_last_attempt(fx):
    env = FakeEnv(fx["records"])
    _run(fx, env, ["reference"])
    assert [e["TP_RUN"] for _, e in env.calls] == [f"x1.reference.a{i}" for i in (1, 2, 3)]
    assert "TP_SHUFFLE" in env.calls[2][1] and "TP_SHUFFLE" not in env.calls[0][1]


def test_seeded_variant_consistent_fail_and_variant_stack_torn_down(fx):
    env = FakeEnv(fx["records"])
    body = _run(fx, env, ["reference", "bug:BUG_OWNERSHIP_UPDATE"]).outputs["run_records"]
    assert _by(body, "bug:BUG_OWNERSHIP_UPDATE")[OWN]["verdict"] == "CONSISTENT_FAIL"
    assert _by(body, "reference")[OWN]["verdict"] == "PASS"
    assert env.down_called == ["bug:BUG_OWNERSHIP_UPDATE"]  # reference stays up


def test_dead_db_mid_run_gives_zero_verdicts_after_it(fx):
    body = _run(fx, FakeEnv(fx["records"], die_after=1), ["reference"]).outputs["run_records"]
    verdicts = {v["verdict"] for v in body["verdicts"]}
    assert verdicts <= {"SUSPECT_ENV", "NOT_RUN"}  # attempt 2's post-probe fails; 3 never runs
    assert not verdicts & {"PASS", "CONSISTENT_FAIL", "INTERMITTENT"}


def test_intermittent_is_escalated_when_stable_on_reference(fx):
    env = FakeEnv(fx["records"], flaky=True)
    body = _run(fx, env, ["reference", "bug:BUG_FLAKE_RACE"]).outputs["run_records"]
    v = _by(body, "bug:BUG_FLAKE_RACE")[RACE]
    assert v["verdict"] == "INTERMITTENT" and v["dist"] == "1/3"
    assert v["escalation"]["escalated"] is True and v["escalation"]["reference"] == "0/10"
    assert v["escalation"]["variant"].endswith("/10")


def test_records_are_sealed_and_verify(fx, data):
    body = _run(fx, FakeEnv(fx["records"]), ["reference"]).outputs["run_records"]
    folder = db.data_dir() / body["folder"]
    assert record.verify(folder, body["root"]) == []
    rows = record.rows(folder)
    assert len(rows) == 59 * 3 and rows[0]["id"].startswith("ex:reference/a1/")
    assert (folder / "logs" / "reference.log").exists()
    assert not (folder.with_suffix(".work")).exists()
    again = _run(fx, FakeEnv(fx["records"]), ["reference"])
    assert again.status == "failed" and "sealed" in again.reason
