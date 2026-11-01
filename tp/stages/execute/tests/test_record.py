import os
import stat

import pytest

from tp.stages.execute import record


def test_sealed_folder_verifies_and_is_read_only(tmp_path):
    root = record.write(tmp_path / "r", {"results.jsonl": "{}\n", "logs/ref.log": "x\n"})
    assert record.verify(tmp_path / "r", root) == []
    mode = (tmp_path / "r" / "results.jsonl").stat().st_mode
    assert not mode & stat.S_IWUSR


def test_tampering_is_detected(tmp_path):
    f = tmp_path / "r"
    root = record.write(f, {"results.jsonl": '{"outcome": "failed"}\n', "probes.json": "[]"})
    os.chmod(f / "results.jsonl", 0o644)
    (f / "results.jsonl").write_text('{"outcome": "passed"}\n')
    (f / "extra.txt").write_text("planted")
    (f / "probes.json").unlink()
    assert sorted(record.verify(f, root)) == [
        "changed: results.jsonl", "missing: probes.json", "unlisted: extra.txt"]  # fmt: skip


def test_a_rewritten_seal_breaks_the_root_hash(tmp_path):
    f = tmp_path / "r"
    root = record.write(f, {"results.jsonl": "a\n"})
    os.chmod(f / "results.jsonl", 0o644)
    os.chmod(f / record.SEAL, 0o644)
    (f / "results.jsonl").write_text("b\n")
    import hashlib

    (f / record.SEAL).write_text(f"{hashlib.sha256(b'b\n').hexdigest()}  results.jsonl\n")
    assert record.verify(f, root) == ["records.sha256 changed (root hash)"]


def test_a_sealed_folder_is_never_overwritten(tmp_path):
    record.write(tmp_path / "r", {"a": "1"})
    with pytest.raises(FileExistsError):
        record.write(tmp_path / "r", {"a": "2"})
