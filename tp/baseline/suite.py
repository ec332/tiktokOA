"""Run one in-process test folder (arms a and b) over the fixed variants and mutants (B1-B3).

Through the checks strategy, in_process mode: the SUT app in the runner, on the reference DB, with
the variant's flags as settings."""

import shutil
import subprocess
from pathlib import Path

from tp.core import profile, sut
from tp.stages.gate import handlers
from tp.stages.gate import mutants as mutant_src

HERE = Path(__file__).parent
K = 3  # runs per variant, as the platform's execute (x252)
FIX = "fix-pagination"  # the real bug O-6's fix (retest V2)
MUTANT_TIMEOUT = "15"  # seconds per test under a mutant, as the gate
PLUGIN = {"/tp_env/mutant_phase.py": HERE / "mutant_phase.py"}
# TP_MODE=network keeps the env harness's per-test rollback and leak guard off: these suites
# manage their own DB (they commit, and delete everything at session end).
QUIET = {"TP_MODE": "network"}


def variants() -> list[str]:
    return ["reference", *(f"bug:{f}" for f in sut.load()["flags"]), f"fixed:{FIX}"]


def fixed_mounts(src: Path, patch: str, work: Path) -> dict[str, Path]:
    """fixed:<patch> in process: the runner image holds the reference code, so overlay the
    files the patch touches, patched, read-only (the mutant mechanism)."""
    p = sut.patches_dir() / f"{patch}.patch"
    files = sut._touched(p)
    shutil.rmtree(work, ignore_errors=True)
    for f in files:
        (work / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f, work / f)
    # `patch`, not `git apply`: work/ sits inside this repo, where git would resolve paths
    # against the repo root.
    subprocess.run(["patch", "-s", "-p1", "-d", str(work), "-i", str(p.resolve())], check=True)
    return {mutant_src.image_root() + mutant_src.rel(f): work / f for f in files}


def run(env, tests: Path, out: Path, variant: str, extra_env=None) -> list[dict]:
    """One pytest run; platform_rt's JSONL records (nodeid, outcome, failure.phase/error)."""
    mounts = dict(PLUGIN)
    if variant.startswith("fixed:"):
        mounts |= fixed_mounts(Path(env.code_scope()["src"]), variant[6:], out / "overlay")
        variant = "reference"
    return env.pytest(tests, out, variant, env=QUIET | (extra_env or {}), mounts=mounts,
                      args=("-p", "mutant_phase"))["results"]  # fmt: skip


def outcomes(records: list[dict]) -> dict[str, str]:
    return {r["nodeid"]: r["outcome"] for r in records}


def measure(env, tests: Path, work: Path, muts: dict, emit=print) -> dict:
    """{variants: {variant: [{node: outcome}] * K}, killed: {node: [mutant]}, mutants: n}.
    muts: {"files": mounts, "list": parsed mutants}, generated once for every arm."""
    runs = {}
    for v in variants():
        runs[v] = [outcomes(run(env, tests, work / v.replace(":", "-") / f"k{i}", v))
                   for i in range(K)]  # fmt: skip
        emit(f"  {v}: {sum(o == 'passed' for o in runs[v][0].values())}/{len(runs[v][0])} pass")
    ref = runs["reference"]
    passing = sorted(n for n in ref[0] if all(r.get(n) == "passed" for r in ref))
    killed, asserted = mutate(env, tests, work / "mutants", muts, passing)
    emit(f"  mutants: {len({m for ms in killed.values() for m in ms})}/{len(muts['list'])} killed")
    return {"variants": runs, "killed": killed, "asserted": asserted, "mutants": len(muts["list"])}


def mutate(env, tests: Path, work: Path, muts: dict, passing: list[str]) -> tuple[dict, list]:
    """The gate's method: a stats run links each mutant to the tests that reach it in their
    body, then one pytest per mutant over those tests. Unreached mutants can't be killed.
    -> ({node: [killed mutants]}, [mutants killed by an AssertionError] (design 003 B13))."""
    mounts = PLUGIN | muts["files"]
    nodes = tuple("/work/tests/" + n for n in passing)
    stats = env.pytest(tests, work / "stats", "reference", mounts=mounts,
                       env=QUIET | {"MUTANT_UNDER_TEST": "stats"},
                       args=("-p", "mutant_phase", *nodes))["results"]  # fmt: skip
    reached = {r["nodeid"]: set(r.get("reached", [])) for r in stats if r["outcome"] == "passed"}
    jobs = []
    for m in muts["list"]:
        linked = sorted(n for n, fns in reached.items() if m["function"] in fns)
        if linked:
            jobs.append({"key": m["name"], "env": {"MUTANT_UNDER_TEST": m["name"],
                                                   "TP_TIMEOUT": MUTANT_TIMEOUT},
                         "args": ["-p", "mutant_phase", *("/work/tests/" + n for n in linked)]})  # fmt: skip
    matrix = env.pytest(tests, work / "matrix", "reference", mounts=mounts, env=QUIET,
                        matrix=jobs)["matrix"] if jobs else {}  # fmt: skip
    killed: dict[str, list] = {}
    asserted = set()
    for name, recs in matrix.items():
        for r in recs:
            failure = r.get("failure") or {}
            if r["outcome"] == "failed" and failure.get("phase") == "call":
                killed.setdefault(r["nodeid"], []).append(name)
                if failure.get("error", "").startswith("AssertionError"):
                    asserted.add(name)
    return {n: sorted(ms) for n, ms in sorted(killed.items())}, sorted(asserted)


def generate_mutants(env, work: Path, refs: list) -> dict:
    """mutmut's pool + the gate's handler mutants (K8), sealed blind ones included: the caller
    splits results by pool (design 003 B11-B12)."""
    cs = env.code_scope()
    files = mutant_src.generate(env, cs["mutate"], work)
    h_files, h_ms = handlers.generate(Path(cs["src"]), sut.load()["ref"], refs, work / "handlers")
    return {"files": files | h_files, "list": mutant_src.parse(work, cs["mutate"]) + h_ms}


def tests_dir() -> str:
    """The repo's own tests folder: the profile's `mutation.repo_prefix` + `tests`."""
    return profile.load().mutation.value["repo_prefix"] + "tests"


def sut_tests(src: Path, ref: str, dest: Path, with_tests: bool = True) -> Path:
    """The repo's tests folder at the upstream ref (git, so none of our added test files). Without
    with_tests: only conftest + helpers (the naive arm's fixtures)."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(src), "archive", ref, tests_dir()],
                         check=True, capture_output=True).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(dest), f"--strip-components={len(Path(tests_dir()).parts)}"], input=tar,
                   check=True)  # fmt: skip
    if not with_tests:
        for f in dest.rglob("test_*.py"):
            f.unlink()
    return dest
