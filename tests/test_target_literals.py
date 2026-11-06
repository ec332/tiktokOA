"""Allow-list ratchet (iteration 2, seams/001 D4-D7): no target-specific literals under tp/.

Platform code must not know which project it is testing. Each tracked file under tp/ (tests,
fixtures and expected outputs excluded: they use the template as sample data on purpose) may
contain at most the number of matching lines recorded in the baseline. The baseline can only go
down, and must be lowered as soon as a file improves. It reaches {} at the end of iteration 2.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("target_literals_baseline.json")
EXCLUDED_DIRS = {"tests", "fixtures", "expected", "__pycache__"}
PATTERNS = [re.compile(p) for p in (
    r"backend/", r"/api/v1", r"/app/backend", r"alembic", r"FIRST_SUPERUSER", r"mailpit",
    r"BUG_[A-Z]", r"/items", r"full-stack-fastapi", r"(?i)paperless", r"(?i)\bmemos\b",
)]  # fmt: skip


def counts() -> dict[str, int]:
    files = subprocess.run(["git", "ls-files", "tp"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()  # fmt: skip
    out = {}
    for f in files:
        if EXCLUDED_DIRS & set(Path(f).parts[:-1]):
            continue
        try:
            text = (ROOT / f).read_text()
        except UnicodeDecodeError, FileNotFoundError:
            continue
        n = sum(any(p.search(line) for p in PATTERNS) for line in text.splitlines())
        if n:
            out[f] = n
    return out


def test_no_new_target_literals():
    baseline = json.loads(BASELINE.read_text())
    now = counts()
    grew = {f: f"{baseline.get(f, 0)} -> {n}" for f, n in now.items() if n > baseline.get(f, 0)}
    assert not grew, f"target literals added under tp/ (move them into the profile): {grew}"


def test_baseline_is_tight():
    baseline = json.loads(BASELINE.read_text())
    now = counts()
    loose = {f: f"{n} -> {now.get(f, 0)}" for f, n in baseline.items() if now.get(f, 0) < n}
    assert not loose, f"literals were removed: lower {BASELINE.name} to match: {loose}"
