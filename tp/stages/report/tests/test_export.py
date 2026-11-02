"""Export to the user's repo (M8 report/009): which tests are pre-ticked, and the git side
(new branch from the tested commit, one commit, checkout untouched, never overwrite)."""

import subprocess
from pathlib import Path

import pytest

from tp.stages.report import export


def _row(unit, case, status, stability="STABLE@K5"):
    return {"unit": unit, "case_id": case, "status": status, "stability": stability,
            "outcome": "failed" if status == "CANDIDATE_DEFECT" else "passed"}  # fmt: skip


def _inputs():
    bound = {"tests": [{"unit": u, "file": f"test_{u.lower()}.py", "ac": "O-1", "status": "BOUND",
                        "source": f"def test_{u.lower()}():\n    pass\n"}
                       for u in ("A", "B", "C", "D")]}  # fmt: skip
    gate = {"rows": [_row("A", "A.1", "ADMITTED"), _row("A", "A.2", "CANDIDATE_DEFECT"),
                     _row("B", "B", "WEAK"), _row("C", "C", "ADMITTED", "FLAKY"),
                     _row("D", "D", "CANDIDATE_DEFECT")]}  # fmt: skip
    cases = {"cases": [{"id": u, "priority": p, "purpose": f"purpose {u}"}
                       for u, p in (("A", "P1"), ("B", "P0"), ("C", "P2"), ("D", "P0"))]}  # fmt: skip
    triage = {"findings": [{"case_id": "A.2", "class": "PRODUCT_DEFECT"},
                           {"case_id": "D", "class": "TEST_DEFECT"}], "quarantine": []}  # fmt: skip
    return bound, gate, cases, triage


def test_candidates_pretick_only_trusted_tests_sorted_by_priority():
    c = export.candidates(*_inputs())
    assert [x["unit"] for x in c] == ["B", "D", "A", "C"]
    by = {x["unit"]: x for x in c}
    assert by["A"]["trusted"] and by["A"]["fails_now"] == ["A.2"]
    assert not by["B"]["trusted"] and "weak" in by["B"]["why"][0]
    assert not by["C"]["trusted"] and "flaky" in by["C"]["why"][0]
    assert not by["D"]["trusted"] and "the test itself is wrong" in by["D"]["why"][0]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "backend" / "tests").mkdir(parents=True)
    (r / "backend" / "tests" / "__init__.py").write_text("")
    _git(tmp_path, "init", "-q", "-b", "feature", str(r))
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return r


def test_export_writes_one_commit_on_a_new_branch_and_never_overwrites(repo, tmp_path):
    bound, gate, cases, triage = _inputs()
    chosen = [x for x in export.candidates(bound, gate, cases, triage) if x["trusted"]]
    sources = {t["file"]: t["source"] for t in bound["tests"]}
    harness = {"harness.py": tmp_path / "harness.py", "factories.py": tmp_path / "factories.py"}
    for p in harness.values():
        p.write_text("# support\n")
    ref = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")

    res = export.export(repo, ref, "tp/tests-r-0001", "r-0001", chosen, sources, harness)

    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature"  # checkout untouched
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "tp/tests-r-0001~1") == ref  # one commit on the tested ref
    assert _git(repo, "worktree", "list").count("\n") == 0  # temporary worktree removed
    files = _git(repo, "ls-tree", "-r", "--name-only", "tp/tests-r-0001").splitlines()
    gen = "backend/tests/generated/"
    assert {gen + "test_a.py", gen + "conftest.py", gen + "README.md", gen + "__init__.py",
            gen + "_support/harness.py", gen + "_support/platform_rt/plugin.py"} <= set(files)  # fmt: skip
    assert gen + "test_b.py" not in files and res["tests"] == ["test_a.py"]
    readme = _git(repo, "show", f"tp/tests-r-0001:{gen}README.md")
    assert "Expected to fail until a defect is fixed" in readme and "A.2" in readme

    with pytest.raises(export.ExportError, match="already exists"):
        export.export(repo, ref, "tp/tests-r-0001", "r-0001", chosen, sources, harness)
