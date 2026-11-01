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
    assert service.parse_variant("fixed:fix-pagination", FLAGS) == []  # run_pytest parses it too
    for bad in ["bug:", "bug:BUG_C", "nope", "fixed:"]:
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
    assert contract.resolve("env").version == "1.3.0"
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


def test_fixed_variant_adds_its_patch_to_the_image_but_no_flags(tmp_path, monkeypatch):  # retest D3
    monkeypatch.setattr(service.sut, "checkout", lambda cfg, root: tmp_path)
    ref, fixed = Stack("reference"), Stack("fixed:fix-pagination")
    assert fixed.flags == [] and fixed.fix == "fix-pagination"
    assert list(fixed.patch_hashes) == [*ref.patch_hashes, "fix-pagination"]
    assert fixed.image != ref.image
    with pytest.raises(EnvError, match="no sut/patches/nope.patch"):
        Stack("fixed:nope")


def test_a_fix_patch_left_applied_is_found(tmp_path):  # retest review gap 2
    import subprocess

    src, patches = tmp_path / "src", tmp_path / "sut/patches"
    src.mkdir()
    patches.mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)
    (src / "items.py").write_text("skip = 0\n")
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    (src / "items.py").write_text("skip = max(0, skip)\n")
    (patches / "fix-x.patch").write_text(git("diff").stdout.decode())
    stack = Stack.__new__(Stack)
    stack.root, stack.src, stack.cfg = tmp_path, src, {"patches": []}
    assert stack.stray_patches() == ["fix-x"]  # still applied, as after a hard kill mid-build
    git("checkout", "items.py")
    assert stack.stray_patches() == []
