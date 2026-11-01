import pytest


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    """Every spec test gets its own empty data dir (runs, reviews, llm_cache)."""
    monkeypatch.setenv("TP_DATA", str(tmp_path))
    return tmp_path
