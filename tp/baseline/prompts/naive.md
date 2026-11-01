You are writing an automated test suite for a FastAPI backend. Below is its source code and the pytest fixtures/helpers available to tests.

Write about {target} pytest test functions that check this backend works correctly. Tests run in-process with the `client` fixture (a `fastapi.testclient.TestClient`) and the other fixtures in `tests/conftest.py`; they may import `app.*` and `tests.utils.*`. API paths start with `settings.API_V1_STR`.

Return JSON: `imports` (the module-level import lines every test needs) and `tests` (one entry per test function: its `name` and full `source`, starting with `def test_...`). Each test must be self-contained (no shared module state).

Sample {sample}.

## Source

{source}
