"""Golden test: the committed drafting call replays offline to the committed cases hash.

Input is the frozen golden spec (fixtures/spec.json), as `tp stage run cases --input` gives it.
Re-record after a prompt, template or calculator change (stage_version changes):
    TP_DATA=/tmp/g tp stage run cases --input spec=tp/stages/cases/fixtures/spec.json --mode record
    TP_DATA=/tmp/g tp review resolve <id> --all-lines approve
then copy /tmp/g/llm_cache/*.json to fixtures/llm_cache/ and the cases body hash to golden.json.
"""

import json
import shutil
from pathlib import Path

from tp.core import artifacts, hashing, review, runner
from tp.stages.cases.stage import CasesStage

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_cases_replay_offline_to_the_golden_hash(data):
    golden = json.loads((FIXTURES / "golden.json").read_text())
    assert golden["stage_version"] == CasesStage.version, "stage changed: re-record (see docstring)"
    shutil.copytree(FIXTURES / "llm_cache", data / "llm_cache", dirs_exist_ok=True)
    spec = artifacts.put("spec", json.loads((FIXTURES / "spec.json").read_text()),
                         labels=["pre-generated"])  # fmt: skip
    run_id = runner.start_run("replay")
    runner.rerun(run_id, "cases", overrides={"spec": spec.id})
    item = next(i for i in review.list_(status="open") if i["kind"] == "signoff")
    lines = {ln["id"]: {"decision": "approve"} for ln in item["payload"]["lines"]}
    review.resolve(item["id"], None, "golden", detail={"lines": lines})
    runner.resume(item["id"])
    ex = [e for e in runner.get_run(run_id)["executions"] if e["stage"] == "cases"][-1]
    assert ex["status"] == "succeeded", ex["reason"]
    body = artifacts.get(ex["outputs"]["cases"]).body
    assert body["signoff"]["signed"]
    assert hashing.sha256(body) == golden["cases_hash"]
