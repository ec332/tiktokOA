import subprocess
import sys

import pytest

from tp.core.hashing import canonical, sha256

OBJ = {"b": [1, 2.5, -0.0, "x\r\ny"], "a": {"z": None, "y": True}, "ü": "é"}


def test_key_order_and_normalisation():
    assert canonical(OBJ) == canonical(
        {"ü": "é", "a": {"y": True, "z": None}, "b": [1, 2.5, 0.0, "x\ny"]}
    )


def test_same_hash_across_processes():
    code = f"from tp.core.hashing import sha256; print(sha256({OBJ!r}))"
    other = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "123"},
    ).stdout.strip()
    assert other == sha256(OBJ)


@pytest.mark.parametrize("bad", [float("nan"), {1: "x"}, object()])
def test_rejects_non_canonical(bad):
    with pytest.raises((ValueError, TypeError)):
        sha256(bad)
