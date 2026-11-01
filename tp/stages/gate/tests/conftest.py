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
    return {n: json.loads((FIXTURES / f"{n}.json").read_text())
            for n in ("bound_tests", "cases", "spec")}  # fmt: skip
