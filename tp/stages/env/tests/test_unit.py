"""No Docker needed: variant parsing, naming, config normalisation, the override, wiring."""

import pytest
import yaml

from tp.core import contract
from tp.stages.env import service
from tp.stages.env.service import EnvError, Stack

FLAGS = ["BUG_A", "BUG_B"]


def test_parse_variant():
    assert service.parse_variant("reference", FLAGS) == []
    assert service.parse_variant("bug:BUG_B+BUG_A", FLAGS) == ["BUG_A", "BUG_B"]
    for bad in ["bug:", "bug:BUG_C", "nope", "fixed:BUG_A"]:
        with pytest.raises(EnvError):
            service.parse_variant(bad, FLAGS)


def test_project_name_is_compose_safe():
    assert service.project_name("reference") == "tp_reference"
    assert service.project_name("bug:BUG_A+BUG_B") == "tp_bug_bug_a_bug_b"


def test_normalise_config_strips_machine_paths():
    cfg = {"name": "tp_x", "services": {"b": {"build": {"context": "/home/me/sut/full"}}}}
    out = service.normalise_config(cfg, {"/home/me/sut": "<sut>", "tp_x": "<project>"})
    assert out == {"name": "<project>", "services": {"b": {"build": {"context": "<sut>/full"}}}}


def test_override_sets_every_flag_explicitly_and_a_free_port(tmp_path):
    stack = Stack.__new__(Stack)  # no SUT checkout needed for the override
    stack.cfg = {"flags": FLAGS, "env": {"FASTAPI_ENV": "development"}}
    stack.flags, stack.image, stack.dir = ["BUG_B"], "tp-sut-backend:abc", tmp_path
    stack.write_override()
    backend = yaml.safe_load(stack.override.read_text())["services"]["backend"]
    assert backend["image"] == "tp-sut-backend:abc"
    assert backend["ports"] == ["127.0.0.1::8000"]
    assert backend["environment"]["BUG_A"] == "false"
    assert backend["environment"]["BUG_B"] == "true"
    assert backend["environment"]["FASTAPI_ENV"] == "development"


def test_override_puts_db_and_backend_on_an_internal_network(tmp_path):  # design D16
    stack = Stack.__new__(Stack)
    stack.cfg, stack.flags, stack.image, stack.dir = {"flags": []}, [], "x", tmp_path
    stack.write_override()
    override = yaml.safe_load(stack.override.read_text())
    assert override["networks"] == {"internal": {"internal": True}}
    for svc in ("db", "backend"):
        assert override["services"][svc]["networks"] == ["default", "internal"]
    assert "networks" not in override["services"]["mailpit"]


def test_env_stage_registers_service_and_cli():
    contract.load()
    assert contract.resolve("env").version == "1.1.0"
    assert contract.resolve("env", stub=True).version == "0.0.0-stub"  # stub kept (D11)
    assert contract.SERVICES["env"] is service.SERVICE
    assert "env" in contract.CLIS


def test_runner_built_on_another_patch_set_is_refused(tmp_path, monkeypatch):  # review gap 5
    import json

    import pytest

    from tp.stages.env import service

    class FakeStack:
        cfg, image, project = {"flags": []}, "tp-sut-backend:new", "tp_reference"

        def __init__(self, variant):
            pass

        def state(self):
            return {}

        def reset(self):
            return 0.0

    monkeypatch.setenv("TP_DATA", str(tmp_path))
    monkeypatch.setattr(service, "Stack", FakeStack)
    (tmp_path / "published.json").write_text(
        json.dumps({"runner": {"digest": "sha256:x", "base": "tp-sut-backend:old"}})
    )
    with pytest.raises(service.EnvError, match="tp generate image"):
        service.run_pytest(tmp_path / "tests", tmp_path / "out")
