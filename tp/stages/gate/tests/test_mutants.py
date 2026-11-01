from tp.stages.gate import mutants
from tp.stages.gate.tests.test_stage import CRUD_MUT, CRUD_ORIG


def test_parse_names_lines_and_functions_as_the_trampoline_reports_them(tmp_path):
    for d, text in (("mut", CRUD_MUT), ("orig", CRUD_ORIG)):
        (tmp_path / d / "app").mkdir(parents=True)
        (tmp_path / d / "app/crud.py").write_text(text)
    ms = mutants.parse(tmp_path, ["backend/app/crud.py"])
    assert [m["name"] for m in ms] == ["app.crud.x_f__mutmut_1", "app.crud.x_f__mutmut_2"]
    # stats mode reports `module.x_<fn>` (seen in the real run), not `module.<fn>`
    assert {m["function"] for m in ms} == {"app.crud.x_f"}
    assert [m["line"] for m in ms] == [2, 2] and "-    return x + 1" in ms[0]["diff"]
    assert mutants.rel("backend/app/api/deps.py") == "app/api/deps.py"
    assert mutants.module_of("app/api/deps.py") == "app.api.deps"
