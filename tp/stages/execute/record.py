"""Recorder: a write-once run folder closed by records.sha256 (design D8, D9)."""

import hashlib
import json
import os
import stat
from pathlib import Path

SEAL = "records.sha256"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(folder: Path, files: dict[str, str]) -> str:
    """Write every file, seal the folder, make it read-only. Returns the root hash.
    Refuses a folder that already holds a seal: a record is never overwritten."""
    if (folder / SEAL).exists():
        raise FileExistsError(f"{folder} is sealed; a new attempt needs a new folder")
    for rel, text in files.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    lines = [f"{_sha(folder / rel)}  {rel}" for rel in sorted(files)]
    (folder / SEAL).write_text("\n".join(lines) + "\n")
    for rel in [*files, SEAL]:
        os.chmod(folder / rel, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return _sha(folder / SEAL)


def verify(folder: Path, root: str | None = None) -> list[str]:
    """Problems found (empty = intact): changed, missing or unlisted files, or a seal that
    doesn't match the root hash recorded in the run_records artifact."""
    seal = folder / SEAL
    if not seal.exists():
        return [f"{SEAL} missing"]
    problems = [] if root is None or _sha(seal) == root else [f"{SEAL} changed (root hash)"]
    listed = {}
    for line in seal.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    for rel, digest in listed.items():
        p = folder / rel
        if not p.exists():
            problems.append(f"missing: {rel}")
        elif _sha(p) != digest:
            problems.append(f"changed: {rel}")
    present = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()} - {SEAL}
    problems += [f"unlisted: {rel}" for rel in sorted(present - set(listed))]
    return problems


def rows(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
