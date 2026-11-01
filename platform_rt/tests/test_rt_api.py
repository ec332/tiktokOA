import pytest
from fastapi.testclient import TestClient
from platform_rt.api import ConformanceError, conforms
from platform_rt.expect import ExpectationFailed, MissingObservation, status_matches
from rt_app import app

from platform_rt import ABSENT, api, expect, observe, record


@pytest.fixture(autouse=True)
def client(monkeypatch):
    monkeypatch.setenv("TP_RUN", "r-1")
    record.start("t.py::test_x[TC-1.a]", "TC-1.a")
    api.use(TestClient(app, raise_server_exceptions=False))
    yield
    api.use(None)


def test_requests_carry_trace_headers_and_are_recorded():
    api.get("/api/v1/items/", params={"skip": 1, "limit": 2})
    api.post("/api/v1/items/", json={"title": "a"}, setup=True)
    first, second = record.current().exchanges
    assert first["x_test_trace"] == "r-1/TC-1.a/1" and second["x_test_trace"] == "r-1/TC-1.a/2"
    version, trace, span, flags = first["traceparent"].split("-")
    assert (version, flags, len(trace), len(span)) == ("00", "01", 32, 16)
    assert trace == record.current().trace_id and trace in second["traceparent"]
    assert first["query"] == {"skip": "1", "limit": "2"} and first["body"]["count"] == 5
    assert (second["setup"], second["json"]) == (True, {"title": "a"})


@pytest.mark.parametrize(
    ("exchange", "case_data", "wrong"),
    [
        ({"query": {"skip": "-1"}, "json": None}, {"skip": -1}, []),
        ({"query": {"skip": "0"}, "json": None}, {"skip": -1}, ["skip"]),
        ({"query": {}, "json": {"title": None}}, {"title": None}, []),
        ({"query": {}, "json": {"title": "x"}}, {"title": None}, ["title"]),
        ({"query": {}, "json": {"n": 1}}, {"n": "1"}, ["n"]),  # body types must match
        ({"query": {}, "json": {"a": 1}}, {"title": ABSENT}, []),
        ({"query": {}, "json": {"title": "x"}}, {"title": ABSENT}, ["title"]),
        ({"query": {}, "json": None}, {"title": None}, ["title"]),  # None can't be in a query
    ],
)
def test_conforms(exchange, case_data, wrong):
    assert conforms(exchange, case_data) == wrong


def test_under_test_is_the_request_carrying_case_data(monkeypatch):
    api.get("/api/v1/items/", params={"skip": -1})
    api.get("/api/v1/items/", params={"skip": 0})  # a later, different request
    assert api.under_test({"skip": -1})["step"] == 1
    with pytest.raises(ConformanceError, match="wrong: \\['skip'\\]"):
        api.under_test({"skip": 7})
    monkeypatch.setenv("TP_CONFORMANCE", "0")  # guards v1: no conformance
    assert api.under_test({"skip": 7})["step"] == 2


def test_setup_requests_are_never_under_test():
    api.post("/api/v1/items/", json={"title": "a"}, setup=True)
    with pytest.raises(ConformanceError, match="sent no request"):
        api.under_test({"title": "a"})


@pytest.mark.parametrize(
    ("got", "exp", "ok"),
    [(200, "2xx", True), (201, "2xx", True), (422, "4xx", True), (422, "422", True),
     (500, "4xx", False), (403, 404, False)],
)  # fmt: skip
def test_status_matches(got, exp, ok):
    assert status_matches(got, exp) is ok


def test_expect_records_assertion_ids():
    r = api.get("/api/v1/items/", params={"limit": 2})
    observe("data_len", len(r.json()["data"]))
    ex = api.under_test({"limit": 2})
    expect.status(ex, "2xx", id="TC-1.a/status")
    expect.equals("data_len", 2, id="TC-1.a/data_len")
    with pytest.raises(ExpectationFailed, match=r"\[TC-1.a/x\]"):
        expect.equals("data_len", 3, id="TC-1.a/x")
    with pytest.raises(MissingObservation):
        expect.equals("count", 5, id="TC-1.a/count")
    ids = [(a["id"], a["ok"]) for a in record.current().assertions]
    assert ids == [("TC-1.a/status", True), ("TC-1.a/data_len", True), ("TC-1.a/x", False)]


def test_route_matches_templates():
    from platform_rt.api import route_matches

    assert route_matches("GET /api/v1/items/{id}", "get", "/api/v1/items/3f2a")
    assert not route_matches("GET /api/v1/items/{id}", "PUT", "/api/v1/items/3f2a")
    assert not route_matches("GET /api/v1/items/{id}", "GET", "/api/v1/items/")


def test_under_test_must_use_the_cases_route_and_auth():  # review gaps 1 + 2
    api.post("/api/v1/items/", json={"title": None})  # carries the data, but the wrong route
    with pytest.raises(ConformanceError, match="no request went to"):
        api.under_test({"title": None}, {"routes": ["PUT /api/v1/items/{id}"], "auth": None})
    get = {"routes": ["GET /api/v1/items/"], "auth": False}
    api.get("/api/v1/items/", token="t", params={"skip": 1})  # right route, but with a token
    with pytest.raises(ConformanceError, match="token=no"):
        api.under_test({"skip": 1}, get)
    api.get("/api/v1/items/", params={"skip": 1})
    assert api.under_test({"skip": 1}, get)["step"] == 3
    assert record.current().exchanges[1]["auth"] is True


def test_observations_must_come_from_the_response_under_test():  # review gap 4
    from platform_rt.expect import grounded

    body = {"data": [{"id": 1}, {"id": 2}], "count": 5}
    assert grounded(5, body) and grounded(2, body)  # a value, a list length
    assert not grounded(7, body) and not grounded("5", body)
    assert grounded([3, 2, 1], body)  # lists are derived by the binding: not checked
    r = api.get("/api/v1/items/", params={"limit": 2})
    observe("data_len", len(r.json()["data"]))
    observe("count", 99)  # made up
    api.under_test({"limit": 2})
    expect.equals("data_len", 2, id="TC-1.a/data_len")
    with pytest.raises(ConformanceError, match="count=99 is not in the response"):
        expect.equals("count", 99, id="TC-1.a/count")


def test_a_422_for_a_field_the_case_never_omitted_is_the_bindings():  # G4 run
    from platform_rt.origin import incomplete_request

    missing = {"status": 422, "body": {"detail": [{"type": "missing", "loc": ["body", "title"]}]}}
    assert incomplete_request(missing, {}) == ["title"]
    assert incomplete_request(missing, {"title": "<absent>"}) == []  # the case omits it on purpose
    too_long = {
        "status": 422,
        "body": {"detail": [{"type": "string_too_long", "loc": ["body", "title"]}]},
    }
    assert incomplete_request(too_long, {"title": "x" * 256}) == []


def test_a_mutant_is_off_during_setup_requests(monkeypatch):  # gate: setup runs unmutated
    monkeypatch.setenv("MUTANT_UNDER_TEST", "app.crud.x_f__mutmut_1")
    assert api.get("/api/v1/mutant/", setup=True).json() == {"mutant": None}
    assert api.get("/api/v1/mutant/").json() == {"mutant": "app.crud.x_f__mutmut_1"}
