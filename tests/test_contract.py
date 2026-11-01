from typing import ClassVar

from tp.core import contract, runner
from tp.core.contract import StageResult
from tp.core.stubs import STUBS


class Dummy:
    name, version = "spec", "9.9.9"
    consumes: ClassVar[dict] = {}
    produces: ClassVar[dict] = {"spec": 1}

    def run(self, ctx, inputs):
        return StageResult.succeeded({"spec": {"dummy": True}})


def test_dummy_stage_registers_and_runs(monkeypatch):
    monkeypatch.setitem(contract.REGISTRY, "spec", Dummy())
    run_id = runner.start_run()
    ex = runner.rerun(run_id, "spec")
    assert ex["status"] == "succeeded" and ex["stage_version"] == "9.9.9"


def test_every_consumed_type_is_produced_upstream_at_that_version():
    produced: dict[str, int] = dict(runner.RUN_INPUTS)  # given to the run, e.g. requirements
    for name in runner.PIPELINE:
        stage = contract.resolve(name)
        for t, v in stage.consumes.items():
            assert produced.get(t) == v, (
                f"{name} consumes {t} v{v}, upstream gives {produced.get(t)}"
            )
        produced |= stage.produces
    assert len(STUBS) == 9
