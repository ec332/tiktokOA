import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture(scope="session")
def fx():
    return {
        "gate_report": json.loads((FIXTURES / "gate_report.json").read_text()),
        "bound_tests": json.loads((FIXTURES / "bound_tests.json").read_text()),
        "records": [
            json.loads(x) for x in (FIXTURES / "results_reference.jsonl").read_text().splitlines()
        ],
    }
