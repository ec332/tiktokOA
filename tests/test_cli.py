import json

from typer.testing import CliRunner

from tp.cli.main import app
from tp.core import runner

cli = CliRunner()


def test_tp_run_stub_unattended_produces_nine_artifacts():
    r = cli.invoke(app, ["run", "--stub", "--unattended"])
    assert r.exit_code == 0, r.output
    assert r.output.count("succeeded") == 10  # run line + nine stages
    assert len(runner.heads(1)) == 9


def test_review_resolve_resumes_from_cli():
    r = cli.invoke(app, ["run", "--stub"])
    assert "awaiting_review" in r.output and "tp review resolve 1" in r.output
    r = cli.invoke(app, ["review", "resolve", "1", "--line", "TC-1=approve"])
    assert r.exit_code == 1 and "missing ['TC-2', 'TC-3']" in r.output
    r = cli.invoke(
        app,
        ["review", "resolve", "1", "--all-lines", "approve", "--line", "TC-2=reject:off by one"],
    )
    assert r.exit_code == 0 and "review 2 (signoff) open" in r.output
    r = cli.invoke(app, ["review", "resolve", "2", "--all-lines", "approve"])
    assert r.exit_code == 0 and "r-0001  succeeded" in r.output


def test_replay_run_works_after_recorded_run():
    cli.invoke(app, ["run", "--stub", "--unattended", "--mode", "record"])
    r = cli.invoke(app, ["run", "--stub", "--unattended", "--replay"])
    assert r.exit_code == 0 and "mode=replay" in r.output


def test_stage_run_with_fixture_file_and_from_run(tmp_path):
    fx = tmp_path / "cases.json"
    fx.write_text(json.dumps({"hand": "written"}))
    cli.invoke(app, ["run", "--stub", "--unattended"])
    r = cli.invoke(
        app, ["stage", "run", "gate", "--stub", "--from-run", "r-0001", "--input", f"cases={fx}"]
    )
    assert r.exit_code == 0, r.output
    ex = runner._latest(2, "gate")
    assert ex["inputs"]["spec"] == runner.heads(1)["spec"]
    assert ex["inputs"]["cases"] != runner.heads(1)["cases"]
    out = cli.invoke(app, ["artifact", "show", ex["outputs"]["gate_report"]]).output
    assert "pre-generated" in out


def test_folder_check_passes_on_this_repo():
    assert cli.invoke(app, ["check-folders"]).exit_code == 0


def test_stage_panel_folder_owned_by_its_stage():
    from pathlib import Path

    import yaml

    from tp.core.folders import owner_of

    folders = yaml.safe_load(Path("docs/folders.yaml").read_text())["folders"]
    assert owner_of("web/src/panels/cases/index.tsx", folders) == "cases"
    assert owner_of("web/src/panels/registry.ts", folders) == "skeleton"
