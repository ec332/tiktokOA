"""P1 proof test: one live run per target at a time (harden/001 H1, review L3)."""

import pytest

from tp.core import runner


def test_a_second_live_run_on_the_same_target_is_refused():
    first = runner.start_run()
    assert runner.run_target(first)  # pinned to the selected target (the template)
    runner._set_run(first, "running")  # mid-run, or crashed: the table still says so
    with pytest.raises(ValueError, match="target busy: r-0001"):
        runner.start_run()
    assert runner.start_run(stub=True)  # a stub run touches no target
    runner.cancel(first)  # what the refusal tells the user to do
    second = runner.start_run()
    runner._set_run(first, "running")  # a resumed old run collides at its next stage instead
    ex = runner._execute(second, runner._latest(second, "spec")["id"])
    assert ex["status"] == "failed" and "target busy: r-0001" in ex["reason"]
