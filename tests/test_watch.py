"""Scan and watch (autonomy/001 F9): the changed-code -> routes map and one poll of a fork."""

import subprocess

import pytest
import typer

from tp.cli import watch


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_changed_routes_are_the_handlers_in_changed_files():
    routes = [{"method": "PUT", "path": "/items/{id}", "file": "app/items.py", "line": 9},
              {"method": "GET", "path": "/users/", "file": "app/users.py", "line": 3}]  # fmt: skip
    assert watch.changed_routes({"app/items.py": [10]}, routes) == ["PUT /items/{id}"]
    assert watch.changed_routes({"app/models.py": [1]}, routes) == []


def test_a_new_head_on_the_fork_starts_one_scan(tmp_path):
    fork, repo, dev = tmp_path / "fork.git", tmp_path / "clone", tmp_path / "dev"
    git("init", "--bare", "-q", str(fork), cwd=tmp_path)
    git("clone", "-q", str(fork), str(dev), cwd=tmp_path)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        git("config", k, v, cwd=dev)
    git("commit", "-q", "--allow-empty", "-m", "one", cwd=dev)
    git("push", "-q", "origin", "HEAD:feature", cwd=dev)
    git("clone", "-q", str(fork), str(repo), cwd=tmp_path)
    state, started = tmp_path / "w.json", []
    first = watch.tick(state, str(repo), "feature", started.append)
    assert started == [first] and watch.tick(state, str(repo), "feature", started.append) is None
    git("commit", "-q", "--allow-empty", "-m", "two", cwd=dev)
    git("push", "-q", "origin", "HEAD:feature", cwd=dev)
    second = watch.tick(state, str(repo), "feature", started.append)
    assert started == [first, second] and second != first
    assert git("rev-parse", "feature", cwd=repo).strip() == second  # fetched before the Scan
    git("checkout", "-q", "feature", cwd=repo)  # the branch checked out: refused, nothing started
    git("commit", "-q", "--allow-empty", "-m", "three", cwd=dev)
    git("push", "-q", "origin", "HEAD:feature", cwd=dev)
    with pytest.raises(typer.BadParameter, match="checkout --detach"):
        watch.tick(state, str(repo), "feature", started.append)
    assert started == [first, second]


def test_every_parses_seconds_minutes_hours():
    assert [watch.seconds(s) for s in ("30s", "5m", "1h")] == [30, 300, 3600]
