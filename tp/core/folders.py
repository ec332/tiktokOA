"""Mechanical check of the folder rule (plan 1.4), driven by docs/folders.yaml."""

import re
import subprocess
from pathlib import Path

import yaml

STATUS_DOCS = re.compile(r"^docs/status/([^/]+)/")


def owner_of(path: str, folders: list[dict]) -> str | None:
    if m := STATUS_DOCS.match(path):
        return m.group(1)  # each stage owns its own status log folder
    hits = [f for f in folders if path == f["path"] or path.startswith(f["path"] + "/")]
    return max(hits, key=lambda f: len(f["path"]))["owner"] if hits else None


def check(root: Path, commits: str | None = None) -> list[str]:
    cfg = yaml.safe_load((root / "docs/folders.yaml").read_text())
    order = cfg["milestones"]
    current = order.index(cfg["current"])
    problems = [
        f"{f['path']} exists before its milestone {f['milestone']} (current {cfg['current']})"
        for f in cfg["folders"]
        if order.index(f["milestone"]) > current and (root / f["path"]).exists()
    ]
    if commits:
        log = subprocess.run(
            ["git", "log", "--format=%H %s", commits],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for line in log:
            sha, subject = line.split(" ", 1)
            tag = re.match(r"\[([\w-]+)\]", subject)
            if not tag:
                continue
            files = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            for path in files:
                owner = owner_of(path, cfg["folders"])
                if owner and owner != tag.group(1):
                    problems.append(f"{sha[:7]} [{tag.group(1)}] touches {path} (owned by {owner})")
    return problems
