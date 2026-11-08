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
    patches = root / "targets/app/patches"
    patches.mkdir(parents=True)
    (root / "targets/app/profile.yaml").touch()
    (repo / "a.txt").write_text("two\n")
    (patches / "first.patch").write_text(git(repo, "diff") + "\n")
    git(repo, "commit", "-qam", "tmp")
    (repo / "a.txt").write_text("two\nthree\n")  # overlaps the first patch's context
    (repo / "b.txt").write_text("new\n")
    git(repo, "add", "-N", "b.txt")
    (patches / "second.patch").write_text(git(repo, "diff") + "\n")
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
    (tmp_path / "targets/x").mkdir(parents=True)
    (tmp_path / "targets/x/profile.yaml").write_text("target: {name: x}\n")
    with pytest.raises(sut.SutError, match="missing"):
        sut.load(tmp_path)


PROFILE = """
target: {name: app, repo: x}
commit_tested: y
reach: {source: override, value: {strategy: container, compose: {}}}
spec: {source: override, value: {strategy: served}}
auth: {source: override, value: {strategy: override_module}}
data: {source: override, value: {strategy: override_module}}
state: {source: override, value: {strategy: transaction, db_probe: {sql: s, expect: old},
                                  migrations: app/alembic/versions}}
mutation: {source: override, value: {strategy: in_process, patches: [testinfo, flags, trace],
                                     flags: [BUG_A], code_scope: {exclude: ['app/alembic/**']}}}
safety: {source: override, value: {strategy: unrestricted}}
checks: {source: override, value: {strategy: pytest_cases}}
"""


def test_target_clones_branch_and_overlays(tmp_path, monkeypatch):
    """M8 D10-D14: clone at the branch head, flags patch dropped, head + changed lines read."""
    monkeypatch.setenv("TP_DATA", str(tmp_path / "data"))
    repo = tmp_path / "app"
    (repo / "app/alembic/versions").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    (repo / "app/alembic/versions/a1.py").write_text('revision = "a1"\ndown_revision = None\n')
    (repo / "app/models.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "app/alembic/versions/b2.py").write_text('revision = "b2"\ndown_revision = "a1"\n')
    (repo / "app/models.py").write_text("x = 1\ny = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feat")
    git(repo, "checkout", "-q", "master")
    root = tmp_path / "platform"
    (root / "targets/app").mkdir(parents=True)
    (root / "targets/app/profile.yaml").write_text(PROFILE)
    t = sut.set_target(str(repo), "feature", "master", root=root)
    assert t["migrations_head"] == "b2" and t["changed"] == {"app/models.py": [2]}
    assert Path(t["clone"]) != repo and (Path(t["clone"]) / "app/alembic/versions/b2.py").exists()
    assert git(repo, "branch", "--show-current") == "master"  # the user's checkout untouched
    cfg = sut.load(root)
    assert cfg["patches"] == ["testinfo", "trace"] and cfg["flags"] == []
    assert cfg["ref"] == git(repo, "rev-parse", "feature")
    assert cfg["lifecycle"]["db_probe"]["expect"] == "b2"
    assert cfg["code_scope"]["mutate"] == ["app/models.py"]
    assert sut.branches(str(repo)) == ["feature", "master"]
    sut.set_target(str(repo), "feature", "master", seeded=True, root=root)
    assert sut.load(root)["patches"] == ["testinfo", "flags", "trace"]
    sut.clear_target()
    assert sut.load(root)["ref"] == "y"
