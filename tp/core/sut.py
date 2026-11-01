"""SUT workspace (plan 3.1): read sut.yaml, apply/verify/reset patches in place."""

import re
import subprocess
from pathlib import Path

import yaml

from tp.core import db

REQUIRED = ("name", "repo", "ref", "patches", "compose", "lifecycle")
URL = re.compile(r"^(\w+://|git@)")


class SutError(Exception):
    pass


def load(root: Path = Path(".")) -> dict:
    cfg = yaml.safe_load((root / "sut/sut.yaml").read_text())
    if missing := [k for k in REQUIRED if k not in cfg]:
        raise SutError(f"sut.yaml is missing {missing}")
    return cfg


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], check=False, cwd=path, capture_output=True, text=True)
    if check and r.returncode:
        raise SutError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def checkout(cfg: dict, root: Path = Path(".")) -> Path:
    """The working tree to patch: the local repo itself, or our one-time clone of a URL."""
    if not URL.match(cfg["repo"]):
        path = (root / cfg["repo"]).expanduser().resolve()
        if not (path / ".git").exists():
            raise SutError(f"{path} is not a git repository")
        return path
    path = db.data_dir() / "sut" / cfg["name"]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(path.parent, "clone", "-q", cfg["repo"], path.name)
        _git(path, "checkout", "-q", "--detach", cfg["ref"])
    return path


def _patch(root: Path, name: str) -> Path:
    return (root / "sut/patches" / f"{name}.patch").resolve()


def _touched(patch: Path) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.MULTILINE))


def _applied(path: Path, patch: Path) -> bool:
    return _git(path, "apply", "-R", "--check", str(patch), check=False).returncode == 0


def _guard(cfg: dict, path: Path, patches: list[Path]) -> None:
    """Refuse to touch a tree at the wrong commit or with changes we didn't make."""
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    ref = _git(path, "rev-parse", "--verify", cfg["ref"] + "^{commit}").stdout.strip()
    if head != ref:
        raise SutError(f"{path} is at {head[:7]}, sut.yaml pins {ref[:7]}; check out the ref")
    ours = set().union(*(_touched(p) for p in patches))
    status = _git(path, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    if foreign := sorted({line[3:] for line in status} - ours):
        raise SutError(f"{path} has changes the patches don't explain: {foreign}")


def _applied_count(path: Path, patches: list[Path]) -> int:
    """Patches stack in order, so the applied ones are a prefix; the top one reverses cleanly."""
    for i in range(len(patches), 0, -1):
        if _applied(path, patches[i - 1]):
            return i
    return 0


def verify(cfg: dict, root: Path = Path(".")) -> list[tuple[str, str]]:
    """Apply every patch in order; already-applied ones are skipped. Returns (patch, status)."""
    path = checkout(cfg, root)
    patches = [_patch(root, n) for n in cfg["patches"]]
    _guard(cfg, path, patches)
    done = _applied_count(path, patches)
    out = [(name, "already applied") for name in cfg["patches"][:done]]
    for name, patch in zip(cfg["patches"][done:], patches[done:], strict=True):
        r = _git(path, "apply", "--check", str(patch), check=False)
        if r.returncode:
            raise SutError(f"{name} does not apply: {r.stderr.strip()}")
        _git(path, "apply", str(patch))
        out.append((name, "applied"))
    return out


def reset(cfg: dict, root: Path = Path(".")) -> list[tuple[str, str]]:
    """Reverse applied patches, last first, leaving the tree at the pinned ref."""
    path = checkout(cfg, root)
    patches = [_patch(root, n) for n in cfg["patches"]]
    _guard(cfg, path, patches)
    done = _applied_count(path, patches)
    out = [(name, "not applied") for name in reversed(cfg["patches"][done:])]
    for i in range(done - 1, -1, -1):
        _git(path, "apply", "-R", str(patches[i]))
        out.append((cfg["patches"][i], "reversed"))
    return out
