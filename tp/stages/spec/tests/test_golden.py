"""Golden test (P5): the committed fixtures replay offline to the committed spec hash.

To (re)record after a prompt or fixture change, with Anthropic credentials set:
    TP_DATA=/tmp/golden tp run --story sut/requirements.md --mode record
    ...answer the review queue as in fixtures/answers.json...
    cp /tmp/golden/llm_cache/*.json tp/stages/spec/fixtures/llm_cache/
and write the frozen spec's body hash to fixtures/golden.json.
"""

import json
import shutil
from pathlib import Path

import pytest

from tp.core import artifacts, hashing, review, runner

FIXTURES = Path(__file__).parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden.json"


@pytest.mark.skipif(not GOLDEN.exists(), reason="no recorded fixtures yet (needs an API key)")
def test_story_fixture_replays_to_the_golden_hash(data):
    golden = json.loads(GOLDEN.read_text())
    shutil.copytree(FIXTURES / "llm_cache", data / "llm_cache", dirs_exist_ok=True)
    story = (Path(__file__).parents[4] / "sut/requirements.md").read_text()
    req = artifacts.put("requirements", {"kind": "story", "text": story})
    run_id = runner.start_run("replay", inputs={"requirements": req.id})
    runner.advance(run_id)
    for _ in range(3):  # up to two question rounds
        for item in review.list_(status="open"):
            if item["kind"] in ("question", "confirm_observed"):
                answer = golden["answers"][
                    item["payload"].get("key") or item["payload"]["criterion"]
                ]
                review.resolve(
                    item["id"], answer["decision"], "golden", detail=answer.get("detail")
                )
                last = item["id"]
        runner.resume(last)
    ex = next(e for e in runner.get_run(run_id)["executions"] if e["stage"] == "spec")
    assert ex["status"] == "succeeded", ex["reason"]
    assert hashing.sha256(artifacts.get(ex["outputs"]["spec"]).body) == golden["spec_hash"]
