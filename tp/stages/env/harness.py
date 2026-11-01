"""InProcessHarness (plan 3.2 E6; env design D13): a pytest plugin, loaded with `-p harness`
inside the runner image (`ctx.env.pytest`). Not imported by the platform itself.

in_process: the SUT app is imported here; `get_db` yields a session joined to an outer
transaction with `create_savepoint`, so the handlers' commits become savepoints and everything
rolls back after the test. The leak guard compares per-table row counts with the counts at
session start: anything committed outside the test's transaction fails that test's teardown.
network: no isolation here; requests go to $TP_BASE_URL.

Both modes give tests one fixture, `make`: the env factories (factories.py), whose requests go
through platform_rt in setup mode so they are not test steps.
"""

import os

import pytest
from factories import Factories

MODE = os.environ.get("TP_MODE", "in_process")
API_BASE = os.environ.get("TP_API_BASE", "/api/v1")


def _counts(engine) -> dict[str, int]:
    from sqlalchemy import inspect, text

    with engine.connect() as c:
        return {
            t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()
            for t in inspect(c).get_table_names()
        }


@pytest.fixture(scope="session")
def _sut():
    if MODE != "in_process":
        yield None
        return
    from app.api.deps import get_db
    from app.core.db import engine
    from app.main import app

    yield app, get_db, engine, _counts(engine)


@pytest.fixture(autouse=True)
def _isolated(_sut):
    from platform_rt import api

    if _sut is None:
        yield
        return
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    app, get_db, engine, seed = _sut
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    api.use(TestClient(app, raise_server_exceptions=False))
    try:
        yield
    finally:
        api.use(None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        outer.rollback()
        conn.close()
        now = _counts(engine)
        if leaked := {t: (seed.get(t), n) for t, n in now.items() if seed.get(t) != n}:
            seed.update(now)  # report the leak once, in the test that caused it
            pytest.fail(f"leak guard: rows changed outside the test's transaction {leaked}")


@pytest.fixture
def make(request) -> Factories:
    from platform_rt import api

    def http(method, path, token=None, json_body=None, form=None):
        r = api.request(method, API_BASE + path, token=token, json=json_body, data=form,
                        setup=True)  # fmt: skip
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    su = (os.environ["FIRST_SUPERUSER"], os.environ["FIRST_SUPERUSER_PASSWORD"])
    return Factories(http, su, request.node.nodeid)
