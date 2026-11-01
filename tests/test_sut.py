import subprocess
from pathlib import Path

import pytest

from tp.core import sut


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, dict]:
    """A platform root with two patches, and a local SUT repo they apply to."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    ref = git(repo, "rev-parse", "HEAD")
    root = tmp_path / "platform"
    (root / "sut/patches").mkdir(parents=True)
    (repo / "a.txt").write_text("two\n")
    (root / "sut/patches/first.patch").write_text(git(repo, "diff") + "\n")
    git(repo, "commit", "-qam", "tmp")
    (repo / "a.txt").write_text("two\nthree\n")  # overlaps the first patch's context
    (repo / "b.txt").write_text("new\n")
    git(repo, "add", "-N", "b.txt")
    (root / "sut/patches/second.patch").write_text(git(repo, "diff") + "\n")
    git(repo, "reset", "-q", "--hard", ref)
    (repo / "b.txt").unlink(missing_ok=True)
    cfg = {
        "name": "app",
        "repo": "../app",
        "ref": ref,
        "patches": ["first", "second"],
        "compose": {},
        "lifecycle": {},
    }
    return root, cfg


def test_verify_applies_in_order_and_is_idempotent(workspace):
    root, cfg = workspace
    assert sut.verify(cfg, root) == [("first", "applied"), ("second", "applied")]
    repo = root.parent / "app"
    assert (repo / "a.txt").read_text() == "two\nthree\n" and (repo / "b.txt").exists()
    assert sut.verify(cfg, root) == [("first", "already applied"), ("second", "already applied")]


def test_reset_reverses_last_first(workspace):
    root, cfg = workspace
    sut.verify(cfg, root)
    assert sut.reset(cfg, root) == [("second", "reversed"), ("first", "reversed")]
    repo = root.parent / "app"
    assert git(repo, "status", "--porcelain") == ""


def test_refuses_foreign_changes(workspace):
    root, cfg = workspace
    (root.parent / "app" / "mine.txt").write_text("user's work\n")
    with pytest.raises(sut.SutError, match="mine.txt"):
        sut.verify(cfg, root)


def test_refuses_wrong_ref(workspace):
    root, cfg = workspace
    repo = root.parent / "app"
    (repo / "c.txt").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "moved on")
    with pytest.raises(sut.SutError, match="pins"):
        sut.verify(cfg, root)


def test_conflicting_patch_is_reported(workspace):
    root, cfg = workspace
    repo = root.parent / "app"
    (repo / "a.txt").write_text("edited by hand\n")  # same file the patch touches
    with pytest.raises(sut.SutError, match="first does not apply"):
        sut.verify(cfg, root)


def test_load_requires_keys(tmp_path):
    (tmp_path / "sut").mkdir()
    (tmp_path / "sut/sut.yaml").write_text("name: x\n")
    with pytest.raises(sut.SutError, match="missing"):
        sut.load(tmp_path)
