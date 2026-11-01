import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="session")
def fx():
    load = lambda n: json.loads((FIXTURES / n).read_text())
    spec = load("spec.json")
    return {"cases": load("cases.json"), "openapi": load("openapi.json"), "spec": spec,
            "criteria": {c["id"]: c for c in spec["criteria"]}}  # fmt: skip


@pytest.fixture
def unit(fx):
    def get(uid):
        u = next(u for u in fx["cases"]["cases"] if u["id"] == uid)
        return u, fx["criteria"][u["ac"]]["routes"]

    return get
