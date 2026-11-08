"""Baseline: detection rules, the naive repair/drop loop (scripted fake model), the setup plugin."""

import json

from tp.baseline import decide, propose

pytest_plugins = ["pytester"]
FIX = "fixed:fix-pagination"


def test_detection_rules_are_flips_across_variants():
    runs = {
        "reference": [{"a": "passed", "b": "passed", "r": "failed", "f": "passed"}] * 3,
        "bug:BUG_TITLE_MAX": [{"a": "failed", "b": "passed", "r": "failed", "f": "passed"}] * 3,
        "bug:BUG_OWNERSHIP_UPDATE": [{"a": "passed", "b": "passed", "r": "failed", "f": "passed"}]
        * 3,
        "bug:BUG_FLAKE_RACE": [
            {"a": "passed", "b": "failed", "r": "failed", "f": "passed"},
            {"a": "passed", "b": "passed", "r": "failed", "f": "passed"},
        ],
        FIX: [{"a": "passed", "b": "passed", "r": "passed", "f": "passed"}] * 3,
    }
    v = decide.verdicts(runs)
    assert v["bug:BUG_FLAKE_RACE"]["b"] == "MIXED"
    assert decide.validates(v, FIX) == {"a": ["BUG_TITLE_MAX"], "r": ["real"]}
    row = decide.row("x", v, FIX, kills=3, mutants=134, race_flag="BUG_FLAKE_RACE")
    assert row["seeded_authored"] == "1/2" and row["seeded_found"] == ["BUG_TITLE_MAX"]
    assert row["real_bug"] and row["race"] == "INTERMITTENT"
    assert (row["bug_validating"], row["tests"], row["strength"]) == (2, 4, 0.022)
    assert "| x | 4 | 1/2 (BUG_TITLE_MAX) |" in decide.markdown([row], ["n"])


def test_a_test_failing_on_reference_never_counts_as_seeded():
    v = {"reference": {"a": "FAIL"}, "bug:BUG_TITLE_MAX": {"a": "FAIL"}, FIX: {"a": "FAIL"}}
    assert decide.validates(v, FIX) == {}


class FakeLLM:
    """Scripted: generation, then repair responses in order."""

    def __init__(self, *responses):
        self.responses, self.prompts = list(responses), []

    def call(self, prompt, **kw):
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


def t(name, body="assert True"):
    return {"name": name, "source": f"def {name}():\n    {body}"}


def test_naive_repairs_failing_tests_then_drops_what_still_fails(tmp_path):
    gen = {"imports": "import os", "tests": [t("test_ok"), t("test_fixable", "assert 0"),
                                             t("test_hopeless", "assert 0")]}  # fmt: skip
    repair = {"imports": "", "tests": [t("test_fixable"), t("test_hopeless", "assert 0")]}
    still = {"imports": "", "tests": [t("test_hopeless", "assert 0")]}
    llm = FakeLLM(gen, repair, still, still)

    def run(folder):  # a test fails when its body is `assert 0`
        src = (folder / propose.TEST_FILE).read_text()
        return [{"nodeid": f"test_naive.py::{n}",
                 "outcome": "failed" if f"def {n}():\n    assert 0" in src else "passed",
                 "failure": {"phase": "call", "error": "AssertionError"}}
                for n in ("test_ok", "test_fixable", "test_hopeless") if f"def {n}(" in src]  # fmt: skip

    body = propose.naive(llm, "SRC {braces}", tmp_path, run, sample=2)
    assert [x["name"] for x in body["tests"]] == ["test_ok", "test_fixable"]
    assert body["dropped"] == {"test_hopeless": "AssertionError"}
    assert (body["repaired"], body["repair_rounds"], body["generated"]) == (["test_fixable"], 3, 3)
    assert "Sample 2." in llm.prompts[0] and "SRC {braces}" in llm.prompts[0]
    assert "test_hopeless" not in (tmp_path / propose.TEST_FILE).read_text()


def test_a_collection_error_fails_every_test():
    recs = [{"nodeid": "test_naive.py", "outcome": "failed", "failure": {"error": "SyntaxError"}}]
    assert propose.failures(recs, ["test_a", "test_b"]) == {"test_a": "SyntaxError",
                                                            "test_b": "SyntaxError"}  # fmt: skip


def test_mutant_is_off_during_setup_and_on_in_the_body(pytester, monkeypatch):
    monkeypatch.setenv("MUTANT_UNDER_TEST", "m1")
    pytester.makepyfile("""
        import os, pytest

        @pytest.fixture
        def seen():
            yield os.environ.get("MUTANT_UNDER_TEST")
            assert "MUTANT_UNDER_TEST" not in os.environ

        def test_body(seen):
            assert seen is None and os.environ["MUTANT_UNDER_TEST"] == "m1"
    """)
    pytester.syspathinsert(str(__import__("pathlib").Path(propose.__file__).parent))
    assert pytester.runpytest("-p", "mutant_phase").ret == 0
