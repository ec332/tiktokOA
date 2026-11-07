import base64
import json

import pytest

from tp.core import artifacts, secrets


@pytest.fixture(autouse=True)
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_DATA", str(tmp_path))
    return tmp_path


def test_every_form_of_a_secret_is_redacted():
    v = secrets.put("t", "admin_password", 'p@ss w/rd+"x"')
    text = " | ".join([v, "p%40ss%20w%2Frd%2B%22x%22", "p%40ss+w%2Frd%2B%22x%22",
                       json.dumps(v)[1:-1], base64.b64encode(v.encode()).decode()])  # fmt: skip
    out = secrets.redact(text)
    assert out.count("$TP_SECRET_ADMIN_PASSWORD") == 5 and "rd" not in out


def test_short_values_are_not_secrets_and_generated_ones_persist():
    secrets.put("t", "user", "admin")
    assert secrets.redact("admin") == "admin"
    a = secrets.get("t", "pw", generate=True)
    assert secrets.get("t", "pw") == a and len(a) >= secrets.MIN_LEN


def test_artifacts_are_sealed_without_secrets(data):
    secrets.put("t", "token", "t0k3n-abcdefgh")
    art = artifacts.put("note", {"log": "Authorization: Token t0k3n-abcdefgh"})
    assert art.body == {"log": "Authorization: Token $TP_SECRET_TOKEN"}
    assert secrets.leaks(data / "artifacts") == []
    (data / "runs" / "x.log").write_text("t0k3n-abcdefgh")
    assert secrets.leaks(data) == [str(data / "runs" / "x.log")]


def test_nothing_changes_without_secrets():
    body = {"a": 1}
    assert secrets.scrub(body) is body


def test_execute_records_are_sealed_without_secrets(data):
    from tp.stages.execute import record

    secrets.put("t", "token", "t0k3n-abcdefgh")
    record.write(data / "rec", {"logs/ref.log": "Authorization: Token t0k3n-abcdefgh"})
    assert secrets.leaks(data / "rec") == []
    assert record.verify(data / "rec") == []  # the seal covers the redacted text
