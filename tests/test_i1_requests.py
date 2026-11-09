"""I1: the merged branches' requests (integration/001): stand-in command, image and host route."""

from tp.stages.env import blackbox as bb

HTTP = {"name": "http", "kind": "http", "image": "runner", "port": 8000, "journal": "/__journal",
        "command": ["python", "-m", "platform_rt.standin", "8000"], "env": {}}  # fmt: skip


def test_a_standin_runs_its_command_and_gets_a_host_forwarder(tmp_path):
    reach = {"service": "app", "port": 80, "standins": [HTTP]}
    out, _ = bb.rewrite({"services": {"app": {"image": "x:1"}}}, tmp_path, tmp_path / "run", reach,
                        {"tp-standin-http": "tp-runner-blackbox:abc"})  # fmt: skip
    s = out["services"]
    assert s["tp-standin-http"]["image"] == "tp-runner-blackbox:abc"  # `runner`, resolved (R3)
    assert s["tp-standin-http"]["command"] == HTTP["command"]
    fwd = s["tp-forwarder-http"]  # R4: the journal is read from the host through this
    assert fwd["networks"] == ["run", "edge"] and fwd["ports"] == ["127.0.0.1::8000"]
    assert fwd["command"][-1] == "TCP:tp-standin-http:8000"
    assert s["tp-standin-http"]["networks"] == ["run"]  # the stand-in itself stays internal


def test_a_dev_mount_of_the_build_context_is_dropped(tmp_path):
    svc = {"build": ".", "volumes": [".:/home/node/app", "/home/node/app/node_modules",
                                     "./seed.sql:/seed.sql", {"type": "bind", "source": "./", "target": "/x"}]}  # fmt: skip
    out, _ = bb.rewrite({"services": {"app": svc}}, tmp_path, tmp_path / "run",
                        {"service": "app", "port": 3000}, {"app": "tp-x-app:abc"})  # fmt: skip
    vols = out["services"]["app"]["volumes"]
    assert vols[0] == "/home/node/app/node_modules"  # anonymous volumes stay
    assert vols[1].endswith("/run/binds/seed.sql:/seed.sql") and len(vols) == 2  # other binds move
