import time

import pytest
from fastapi.testclient import TestClient

from tp.api.app import app, worker


@pytest.fixture
def client():
    yield TestClient(app)
    # One FIFO worker: once this no-op runs, every job the test queued has finished, so none
    # can leak into the next test's data dir (TP_DATA changes per test).
    worker.submit(lambda: None).result(timeout=30)


def wait(client, run_id, status):
    for _ in range(100):
        run = client.get(f"/runs/{run_id}").json()
        if run["status"] == status:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run never reached {status}: {run['status']}")


def test_openapi_has_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in [
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/stages/{name}/rerun",
        "/artifacts/{art_id}",
        "/reviews",
        "/runs/{run_id}/events",
    ]:
        assert p in paths


def test_stub_run_review_resume_and_rerun_over_http(client):
    run = client.post("/runs", json={"stub": True}).json()
    wait(client, run["id"], "awaiting_review")
    [item] = client.get("/reviews", params={"status": "open"}).json()
    assert (
        client.post(f"/reviews/{item['id']}", json={"decision": "nope", "by": "t"}).status_code
        == 409
    )
    lines = {ln["id"]: {"decision": "approve"} for ln in item["payload"]["lines"]}
    r = client.post(f"/reviews/{item['id']}", json={"by": "t", "detail": {"lines": lines}})
    assert r.json()["decision"] == "approve"
    run = wait(client, run["id"], "succeeded")

    gate = next(e for e in run["executions"] if e["stage"] == "gate")
    art = client.get(f"/artifacts/{gate['outputs']['gate_report']}").json()
    assert art["labels"] == ["simulated"] and len(art["parents"]) == 3
    assert client.get(f"/artifacts/{art['parents'][0]}").json()["children"]

    fixture = client.post("/artifacts", json={"type": "cases", "body": {"hand": 1}}).json()
    assert fixture["labels"] == ["pre-generated"]
    client.post(f"/runs/{run['id']}/stages/gate/rerun", json={"inputs": {"cases": fixture["id"]}})
    worker.submit(lambda: None).result(timeout=30)  # rerun job done; stale marks all written
    run = client.get(f"/runs/{run['id']}").json()
    latest = {e["stage"]: e["status"] for e in run["executions"]}
    assert {s for s, st in latest.items() if st == "stale"} == {
        "execute",
        "triage",
        "report",
        "retest",
    }


def test_sse_streams_events_until_idle(client):
    run = client.post("/runs", json={"stub": True, "unattended": True}).json()
    wait(client, run["id"], "succeeded")
    with client.stream("GET", f"/runs/{run['id']}/events") as r:
        body = "".join(r.iter_text())
    assert '"kind": "run_created"' in body and '"kind": "run_succeeded"' in body
    with client.stream(
        "GET", f"/runs/{run['id']}/events", headers={"Last-Event-ID": "999999"}
    ) as r:
        assert "data:" not in "".join(r.iter_text())


def test_stages_lists_the_pipeline_with_contracts(client):
    stages = client.get("/stages").json()
    assert [s["name"] for s in stages][:2] == ["spec", "cases"]
    assert stages[3]["consumes"] == {"bound_tests": 1, "cases": 1, "spec": 1}


def test_unknown_things_404(client):
    assert client.get("/runs/999").status_code == 404
    assert client.get("/artifacts/sha256:00").status_code == 404
