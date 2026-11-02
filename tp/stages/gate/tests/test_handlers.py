"""Handler mutants (K8): switch per mutant, stats hook, flag code left alone, upstream lines."""

import importlib.util
import subprocess
import sys
import types

from tp.stages.gate import handlers

UPSTREAM = '''class R:
    def get(self, path):
        return lambda f: f
router = R()


@router.get("/{id}")
def read(owner, user, found):
    """Doc."""
    if not found:
        return 404
    if owner != user:
        return 403
    return 200
'''
# The flags patch adds lines above the handler and a flag gate inside it.
PATCHED = UPSTREAM.replace("router = R()\n", "router = R()\nsettings = None\nFLAG = 1\n").replace(
    '    """Doc."""\n',
    '    """Doc."""\n    if settings.BUG_X:\n        return 999\n',
)


def _repo(tmp_path):
    f = tmp_path / "backend/app/api/routes/items.py"
    f.parent.mkdir(parents=True)
    f.write_text(UPSTREAM)
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                                    capture_output=True)  # fmt: skip
    git("init", "-q")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "u")
    ref = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()  # fmt: skip
    f.write_text(PATCHED)
    return ref


def _load(path, env, monkeypatch, hits):
    monkeypatch.setenv("MUTANT_UNDER_TEST", env)
    tramp = types.SimpleNamespace(record_trampoline_hit=hits.append)
    monkeypatch.setitem(sys.modules, "mutmut", types.ModuleType("mutmut"))
    monkeypatch.setitem(sys.modules, "mutmut.mutation", types.SimpleNamespace(trampoline=tramp))
    spec = importlib.util.spec_from_file_location("mutated_items", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.settings = types.SimpleNamespace(BUG_X=False)  # the patch's `settings = None` stand-in
    return mod.read


def test_handler_mutants_switch_by_env_and_map_to_upstream_lines(tmp_path, monkeypatch):
    ref = _repo(tmp_path)
    refs = [("backend/app/api/routes/items.py", 12, "C-1")]  # upstream line of `owner != user`
    files, ms = handlers.generate(tmp_path, ref, refs, tmp_path / "out")
    names = [m["name"].rsplit(".", 1)[1] for m in ms]
    # not found / 404 / owner != user / 403 / 200; nothing from the flag gate (999 or BUG_X)
    assert names == [f"x_read__ast_{i}" for i in range(1, 7)]
    by_line = {m["diff"]: m["line"] for m in ms}
    assert by_line["-owner != user\n+owner == user"] == 12  # upstream numbering, like code_refs
    assert all("999" not in m["diff"] and "BUG_X" not in m["diff"] for m in ms)
    (path,) = files.values()
    assert list(files) == ["/app/backend/app/api/routes/items.py"]

    hits: list = []
    assert _load(path, "", monkeypatch, hits)("a", "b", True) == 403
    swap = next(m["name"] for m in ms if m["diff"] == "-owner != user\n+owner == user")
    assert _load(path, swap, monkeypatch, hits)("a", "b", True) == 200
    assert _load(path, "stats", monkeypatch, hits)("a", "a", True) == 200
    assert hits == ["app.api.routes.items.x_read"]


def test_excluded_mutants_leave_numbering_stable(tmp_path):
    ref = _repo(tmp_path)
    refs = [("backend/app/api/routes/items.py", 12, "C-1")]
    _, all_ms = handlers.generate(tmp_path, ref, refs, tmp_path / "a")
    gone = all_ms[0]["name"]
    files, ms = handlers.generate(tmp_path, ref, refs, tmp_path / "b", exclude={gone})
    assert [m["name"] for m in ms] == [m["name"] for m in all_ms[1:]]
    assert gone not in next(iter(files.values())).read_text()


def test_field_constraints_on_changed_lines_become_switched_mutants(tmp_path):
    """M8 gate D21: ge/le swap and +1, size keywords ±1; only changed lines; import-time switch."""
    import os
    import runpy

    src = tmp_path / "src"
    (src / "backend/app").mkdir(parents=True)
    (src / "backend/app/models.py").write_text(
        "def Field(**kw):\n    return kw\n\n"
        "class Item:\n"
        "    title = Field(max_length=255)\n"
        "    price = Field(default=None, ge=0, decimal_places=2)\n"
    )
    files, ms = handlers.fields(src, {"backend/app/models.py": [6]}, tmp_path / "out", set())
    diffs = [m["diff"] for m in ms]
    assert diffs == ["-ge=0\n+gt=0", "-ge=0\n+ge=1", "-decimal_places=2\n+decimal_places=3",
                     "-decimal_places=2\n+decimal_places=1"]  # fmt: skip
    assert all(m["function"] == "*" and m["line"] == 6 for m in ms)
    (path,) = files.values()
    assert runpy.run_path(str(path))["Item"].price == {
        "default": None,
        "ge": 0,
        "decimal_places": 2,
    }
    os.environ["MUTANT_UNDER_TEST"] = ms[0]["name"]
    try:
        assert runpy.run_path(str(path))["Item"].price == {"default": None, "gt": 0,
                                                           "decimal_places": 2}  # fmt: skip
        assert runpy.run_path(str(path))["Item"].title == {"max_length": 255}  # unchanged line
    finally:
        del os.environ["MUTANT_UNDER_TEST"]
