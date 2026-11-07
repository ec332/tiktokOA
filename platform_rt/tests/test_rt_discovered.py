"""platform_rt 0.8.0 (discover/002): the profile's secured map when the spec declares no security
(P1), the runner's token refresh before a JWT expires (P7), and the spec read from the platform's
copy (repo-file specs are not served)."""

import base64
import json
import time

import httpx
import pytest
from platform_rt.api import api as client
from platform_rt.api import token

from platform_rt import families

OPEN_SPEC = {"paths": {"/a": {"post": {}}, "/b": {"get": {}}, "/c": {"get": {}}}}
MAP = {"secured": {"default": "secured", "public": ["POST /a"], "observed_open": ["GET /b"]}}


@pytest.mark.parametrize(("key", "want"), [("GET /c", True), ("GET /b", False), ("POST /a", False)])
def test_the_discovered_map_decides_when_the_spec_declares_no_security(key, want):
    m, p = key.split()
    assert families.secured(OPEN_SPEC, OPEN_SPEC["paths"][p][m.lower()], MAP, key) is want


def test_the_spec_wins_over_the_map_and_no_map_means_not_secured():
    spec = {"security": [{"t": []}], "paths": {"/c": {"get": {"security": [{}]}}}}
    assert families.secured(spec, spec["paths"]["/c"]["get"], MAP, "GET /c") is False  # {} = anon
    assert families.secured(OPEN_SPEC, OPEN_SPEC["paths"]["/c"]["get"], {}, "GET /c") is False


def jwt(ttl: float) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': time.time() + ttl})}.s"


@pytest.fixture
def login(monkeypatch):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"accessToken": "fresh-token"})

    client.use(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t"))
    monkeypatch.setenv(
        "TP_LOGIN",
        json.dumps(
            {
                "path": "/api/auth/signin",
                "token_field": "accessToken",
                "json": True,
                "body": {
                    "credentials": {"username": "tpadmin", "password": "$TP_SECRET_ADMIN_PASSWORD"}
                },
            }
        ),
    )
    monkeypatch.setenv("TP_SECRET_ADMIN_PASSWORD", 'p"w')
    yield sent
    client.use(None)


def test_an_expiring_jwt_is_replaced_by_a_fresh_login(login, monkeypatch):
    monkeypatch.setenv("TP_SECRET_TOKEN", jwt(10))
    assert token() == "fresh-token"
    assert login == [{"credentials": {"username": "tpadmin", "password": 'p"w'}}]
    assert token() == "fresh-token" and len(login) == 1  # not a JWT: kept from now on


@pytest.mark.parametrize("tok", ["plain-token-abcdef", None])
def test_a_live_jwt_or_a_plain_token_is_used_as_it_is(login, monkeypatch, tok):
    tok = tok or jwt(600)
    monkeypatch.setenv("TP_SECRET_TOKEN", tok)
    assert token() == tok
    assert login == []


def test_the_spec_comes_from_the_platforms_copy(tmp_path, monkeypatch):
    f = tmp_path / "openapi.json"
    f.write_text(json.dumps({"openapi": "3.0.3", "paths": {}}))
    monkeypatch.setenv("TP_OPENAPI_FILE", str(f))
    assert client.openapi() == {"openapi": "3.0.3", "paths": {}}
