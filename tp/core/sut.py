"""SUT workspace (plan 3.1): the target's config, apply/verify/reset patches in place.

The config is the active target profile seen through the reverse shim (`profile.legacy`,
seams/001 D2) until every call site reads `ctx.profile` / `ctx.strategies` instead."""

import fnmatch
import json
import re
import subprocess
from pathlib import Path

from tp.core import db, profile

REQUIRED = ("name", "repo", "ref", "patches", "compose", "lifecycle")
URL = re.compile(r"^(\w+://|git@)")


class SutError(Exception):
    pass


def _raw(root: Path) -> dict:
    try:
        return profile.legacy(profile.load(root))
    except profile.ProfileError as e:
        raise SutError(str(e)) from None


def patches_dir(root: Path = Path(".")) -> Path:
    """targets/<slug>/patches/: the target's hand-written patches (seams/001 D1)."""
    return profile.target_dir(root) / "patches"


def load(root: Path = Path(".")) -> dict:
    """The target's config, with the web-chosen target (repo path + branch + base) laid over it
    (M8 D10)."""
    cfg = _raw(root)
    if missing := [k for k in REQUIRED if k not in cfg]:
        raise SutError(f"profile is missing {missing}")
    if t := target():
        _overlay(cfg, t)
    return cfg


# ---- target: a local repo + branch, cloned and tested at the branch head (M8 D10-D14) ----


def _target_file() -> Path:
    return db.data_dir() / "sut" / "target.json"


def target() -> dict | None:
    f = _target_file()
    return json.loads(f.read_text()) if f.exists() else None


def clear_target() -> None:
    _target_file().unlink(missing_ok=True)


def _overlay(cfg: dict, t: dict) -> None:
    seeded = t.get("seeded", False)
    cfg["repo"], cfg["ref"] = t["clone"], t["ref"]
    cfg["patches"] = [p for p in cfg["patches"] if seeded or p != "flags"]  # D12
    cfg["flags"] = cfg.get("flags", []) if seeded else []
    if t.get("alembic_head"):  # D13
        cfg["lifecycle"]["db_probe"]["expect"] = t["alembic_head"]
    scope = cfg.setdefault("code_scope", {})
    if t.get("changed"):  # D14: the branch's changed lines, not the fixed file list
        scope["mutate"] = sorted(t["changed"])
        scope["changed"] = t["changed"]
    cfg["target"] = t


def _alembic_head(tree: Path, versions: str) -> str | None:
    revs, downs = set(), set()
    for f in (tree / versions).glob("*.py"):
        text = f.read_text()
        if m := re.search(r'^revision\s*=\s*["\']([^"\']+)', text, re.MULTILINE):
            revs.add(m.group(1))
        for d in re.findall(r"^down_revision\s*=\s*(.+)$", text, re.MULTILINE):
            downs |= set(re.findall(r'["\']([^"\']+)["\']', d))
    heads = revs - downs
    return heads.pop() if len(heads) == 1 else None


def _changed_lines(tree: Path, base: str, ref: str, exclude: list[str]) -> dict[str, list[int]]:
    """{file: [new-side line numbers]} for app Python the branch changed (not tests/migrations)."""
    diff = _git(tree, "diff", "-U0", f"{base}...{ref}", "--", "*.py").stdout
    out: dict[str, list[int]] = {}
    file = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            file = line[6:] if line.startswith("+++ b/") else None
            if file and ("/tests/" in file or any(fnmatch.fnmatch(file, g) for g in exclude)):
                file = None
        elif file and (m := re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(file, []).extend(range(start, start + count))
    return {f: ls for f, ls in out.items() if ls}


def set_target(repo: str, branch: str, base: str, seeded: bool = False,
               root: Path = Path(".")) -> dict:  # fmt: skip
    """Clone `repo` (once) into data/sut/, fetch, check out `branch`'s head. Never touches
    the user's checkout. Returns the stored target, with warnings."""
    src = Path(repo).expanduser().resolve()
    if not (src / ".git").exists():
        raise SutError(f"{src} is not a git repository")
    raw = _raw(root)
    clone = db.data_dir() / "sut" / f"target-{src.name}"
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, "clone", "-q", "--no-checkout", str(src), clone.name)
    _git(clone, "fetch", "-q", "--prune", str(src), "+refs/heads/*:refs/remotes/origin/*")
    ref, base_ref = (_git(clone, "rev-parse", "--verify", f"origin/{b}^{{commit}}").stdout.strip()
                     for b in (branch, base))  # fmt: skip
    _git(clone, "reset", "-q", "--hard")  # our clone: drop applied patches before moving
    _git(clone, "clean", "-fdq")
    _git(clone, "checkout", "-q", "--detach", ref)
    warnings = []
    dirty = _git(src, "status", "--porcelain").stdout.strip()
    if dirty:
        warnings.append(f"{src} has uncommitted changes; they are not in the tested clone")
    exclude = raw.get("code_scope", {}).get("exclude", [])
    t = {
        "repo": str(src), "branch": branch, "base": base, "ref": ref, "base_ref": base_ref,
        "clone": str(clone.resolve()), "seeded": seeded,
        "alembic_head": _alembic_head(clone, raw["lifecycle"].get("migrations", "")),
        "changed": _changed_lines(clone, base_ref, ref, exclude),
        "subject": _git(clone, "log", "-1", "--format=%s", ref).stdout.strip(),
        "warnings": warnings,
    }  # fmt: skip
    _target_file().write_text(json.dumps(t, indent=2))
    return t


def branches(repo: str) -> list[str]:
    src = Path(repo).expanduser().resolve()
    if not (src / ".git").exists():
        raise SutError(f"{src} is not a git repository")
    out = _git(src, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
    return sorted(out.split())


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
    return (patches_dir(root) / f"{name}.patch").resolve()


def _touched(patch: Path) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.MULTILINE))


def _applied(path: Path, patch: Path) -> bool:
    return _git(path, "apply", "-R", "--check", str(patch), check=False).returncode == 0


def _guard(cfg: dict, path: Path, patches: list[Path]) -> None:
    """Refuse to touch a tree at the wrong commit or with changes we didn't make."""
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    ref = _git(path, "rev-parse", "--verify", cfg["ref"] + "^{commit}").stdout.strip()
    if head != ref:
        raise SutError(f"{path} is at {head[:7]}, the profile pins {ref[:7]}; check out the ref")
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
