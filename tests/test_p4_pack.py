"""P4 (docs/status/domains/001): spec_base (C6), the RealWorld pack against C9, transfer (DR-5)
and the witness rule (DR-6). No Docker: the runs here are stub runs or seeded executions. The
notify fixture pair (SYNTHETIC) runs for real in test_p4_transfer_docker.py."""

import json
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from tp.cli import main
from tp.cli import pack as pack_cli
from tp.core import artifacts, events, runner, transfer
from tp.stages.discover import discovery, spec_base

REALWORLD = pack_cli.PACKS / "realworld"


# ---- spec_base (C6, D12, D13) ------------------------------------------------------------------


@pytest.mark.parametrize(("spec", "want"), [
    ({"servers": [{"url": "/api"}]}, "/api"),
    ({"servers": [{"url": "https://api.example.test/api/v2/"}]}, "/api/v2"),  # host dropped (U1)
    ({"servers": [{"url": "/"}]}, "/fallback"),  # a root says nothing: the paths carry it
    ({"servers": [{"url": "{scheme}://h/{base}"}]}, "/fallback"),  # templated
    ({"servers": []}, "/fallback"),
    ({"swagger": "2.0", "basePath": "/v1"}, "/v1"),
    ({"openapi": "3.0.0"}, "/fallback"),
])  # fmt: skip
def test_base_from_servers_or_basepath(spec, want):
    assert spec_base.base({"paths": {}, **spec}, "/fallback") == want


def test_the_vendored_realworld_spec_sits_under_api():
    spec = yaml.safe_load((REALWORLD / "spec" / "openapi.yml").read_text())
    fallback = discovery.common_base(list(spec["paths"]))
    assert fallback == "" and spec_base.base(spec, fallback) == "/api"  # today's prefix: none


def test_paging_filter_drops_offset_and_limit():
    assert spec_base.paging_filter(["offset", "Limit", "page", "size"]) == ["page", "size"]


# ---- the pack (C9, D6, D7, D11) ----------------------------------------------------------------


def test_the_realworld_pack_validates_against_c9():
    raw, d = pack_cli.load("realworld")
    assert pack_cli.validate(raw, d) == []
    assert raw["suites"] == [] and set(raw) == set(pack_cli.KEYS)
    assert "MIT License" in (d / "spec" / "LICENSE").read_text()
    assert [pack_cli.slug(i) for i in raw["implementations"]] == [
        "aspnetcore-realworld-example-app", "express-bookshelf-realworld-example-app"]  # fmt: skip


def test_a_broken_pack_is_refused(tmp_path):
    raw, d = pack_cli.load("realworld")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "openapi.yml").write_text("openapi: 3.1.0\npaths: {}\n")
    bad = raw | {"answers": ["ok", 3], "implementations": [{"repo": "r", "ref": "main"}],
                 "suites": [{"format": "hurl", "path": "x.hurl"}]}  # fmt: skip
    assert pack_cli.validate(bad, tmp_path) == [
        "spec: spec/openapi.yml doesn't match its sha256",
        "suites[0]: missing sha, source, licence",
        "answers[1]: expected a string",
        "implementations[0]: ref must be a 40-hex commit (SR3)",
    ]
    assert pack_cli.validate({k: v for k, v in raw.items() if k != "brief"}, d) == [
        "brief: expected a str"]  # fmt: skip


def test_targets_name_an_implementation_by_slug_or_a_unique_part():
    impls = pack_cli.load("realworld")[0]["implementations"]
    got = pack_cli.select(impls, "express-bookshelf,aspnetcore")
    assert [pack_cli.slug(i) for i in got] == [
        "express-bookshelf-realworld-example-app", "aspnetcore-realworld-example-app"]  # fmt: skip
    assert pack_cli.select(impls, None) == impls
    with pytest.raises(typer.BadParameter, match="matches 2 of"):
        pack_cli.select(impls, "realworld")


def test_the_brief_carries_the_settled_answers():
    raw = pack_cli.load("realworld")[0]
    text = pack_cli.brief(raw)
    assert text.startswith(raw["brief"]) and all(f"- {a}" in text for a in raw["answers"])


# ---- transfer (DR-5, D1) -----------------------------------------------------------------------


def _target(root: Path, name: str) -> str:
    (root / name).mkdir()
    (root / name / "profile.yaml").write_text("target: {name: x}\n")
    return str(root / name)


def test_transfer_starts_at_the_gate_with_run_a_artifacts(data):
    a = runner.start_run(stub=True, unattended=True, options={"gate": {"k": 2}})
    assert runner.advance(a)["status"] == "succeeded"
    b = transfer.start(a, _target(data, "impl-b"))
    ha, hb = runner.heads(a), runner.heads(b)
    sealed = set(hb) - {"run_options"}
    assert {"spec", "cases", "bound_tests"} <= sealed and "gate_report" not in sealed
    assert {t: hb[t] for t in sealed} == {t: ha[t] for t in sealed}  # same ids, same hashes
    opts = artifacts.get(hb["run_options"]).body
    assert opts["profile"].endswith("impl-b") and opts["gate"] == {"k": 2}
    assert runner.run_target(b).endswith("impl-b")
    st = {e["stage"]: e["status"] for e in runner.get_run(b)["executions"]}
    assert [s for s in runner.PIPELINE if st[s] == "succeeded"] == ["spec", "cases", "generate"]
    ev = next(e for e in events.since(b) if e["kind"] == "run_transferred")
    assert ev["payload"] == {"from_run": f"r-{a:04d}", "bound_tests": ha["bound_tests"]}
    assert runner.advance(b)["status"] == "succeeded"  # the gate on is B's own
    assert runner.heads(b)["bound_tests"] == ha["bound_tests"]


def test_a_run_without_sealed_tests_cannot_be_transferred(data):
    a = runner.start_run(stub=True)
    with pytest.raises(ValueError, match="no succeeded spec, cases, generate"):
        transfer.start(a, _target(data, "impl-b"))


# ---- comparison (DR-6, D9) ---------------------------------------------------------------------


@pytest.mark.parametrize(("cells", "want"), [
    ({"a": "PASS", "b": "PASS"}, ("AGREE", [], ["a", "b"])),
    ({"a": "PASS", "b": "CONSISTENT_FAIL"}, ("SPEC_MISMATCH", ["b"], ["a"])),
    ({"a": "TIMEOUT", "b": "CONSISTENT_FAIL"}, ("TO_PERSON", ["a", "b"], [])),
    ({"a": "PASS", "b": "INTERMITTENT"}, ("INCOMPLETE", [], ["a"])),  # a flaky cell proves nothing
    ({"a": "CONSISTENT_FAIL", "b": None}, ("TO_PERSON", ["a"], [])),
])  # fmt: skip
def test_the_witness_rule(cells, want):
    assert transfer.classify(cells) == want


def _executed(data: Path, name: str, bound: str, verdicts: dict[str, str], records: dict) -> int:
    """A run on target `name` whose generate and execute are seeded with these results."""
    run = runner.start_run(stub=True, options={"profile": _target(data, name)},
                           pipeline=["generate", "execute"])  # fmt: skip
    folder = data / "runs" / f"rec-{name}"
    folder.mkdir(parents=True)
    (folder / "results.jsonl").write_text("".join(json.dumps(
        {"case_id": c, "variant": "reference", "lane": "main", "status": "RAN", **r}) + "\n"
        for c, r in records.items()))  # fmt: skip
    rr = artifacts.put("run_records", {"folder": f"runs/{folder.name}", "verdicts": [
        {"case_id": c, "variant": "reference", "verdict": v} for c, v in verdicts.items()]})  # fmt: skip
    transfer.seed(run, {"generate": {"inputs": {}, "outputs": {"bound_tests": bound}},
                        "execute": {"inputs": {}, "outputs": {"run_records": rr.id}}})  # fmt: skip
    return run


CALLED = "G:dependency_called:POST /api/notes"


def _rec(outcome: str, error: str | None = None) -> dict:
    return {"outcome": outcome, "under_test": "create",
            "exchanges": [{"step": "create", "setup": False, "method": "POST",
                           "path": "/api/notes", "status": 201}],
            "failure": {"error": error} if error else None}  # fmt: skip


def test_compare_prints_the_matrix_with_the_request_and_each_answer(data):
    bound = artifacts.put("bound_tests", {"tests": []}).id
    a = _executed(data, "clean", bound, {CALLED: "PASS", "G:read_back:POST /api/notes": "PASS"},
                  {CALLED: _rec("passed")})  # fmt: skip
    b = _executed(data, "seeded", bound, {CALLED: "CONSISTENT_FAIL",
                                          "G:read_back:POST /api/notes": "PASS"},
                  {CALLED: _rec("failed", "ExpectationFailed: the stand-in never got it\nmore")})  # fmt: skip
    m = transfer.matrix([a, b])
    rows = {r["case"]: r for r in m["rows"]}
    assert rows[CALLED]["class"] == "SPEC_MISMATCH"
    assert (rows[CALLED]["failing"], rows[CALLED]["witnesses"]) == ([f"r-{b:04d}"], [f"r-{a:04d}"])
    assert rows[CALLED]["request"] == "POST /api/notes"
    assert rows[CALLED]["answers"] == {
        f"r-{a:04d}": "201 passed", f"r-{b:04d}": "201 failed: ExpectationFailed: the stand-in never got it"}  # fmt: skip
    assert rows["G:read_back:POST /api/notes"]["class"] == "AGREE"
    out = CliRunner().invoke(main.app, ["compare", f"r-{a:04d}", f"r-{b:04d}"])
    assert out.exit_code == 0, out.output
    assert f"| `{CALLED}` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-{b:04d}; witness r-{a:04d} |" \
        in out.output  # fmt: skip
    assert (
        "Rows: AGREE 1, SPEC_MISMATCH 1" in out.output and "request `POST /api/notes`" in out.output
    )


def test_compare_refuses_runs_of_different_tests(data):
    a = _executed(data, "x", artifacts.put("bound_tests", {"v": 1}).id, {}, {})
    b = _executed(data, "y", artifacts.put("bound_tests", {"v": 2}).id, {}, {})
    out = CliRunner().invoke(main.app, ["compare", f"r-{a:04d}", f"r-{b:04d}"])
    assert out.exit_code == 1 and "ran different bound tests" in out.output


# ---- tp pack run (D7, D8) ----------------------------------------------------------------------


def test_pack_run_onboards_runs_the_first_then_transfers_to_the_rest(data, monkeypatch):
    paths = {"aspnetcore-realworld-example-app": _target(data, "a"),
             "express-bookshelf-realworld-example-app": _target(data, "b")}  # fmt: skip
    monkeypatch.setattr(pack_cli, "onboard", lambda impl: paths[pack_cli.slug(impl)])
    started, transferred = [], []
    real_start = runner.start_run

    def start(*a, **kw):
        started.append(kw)
        return real_start(stub=True, **{k: v for k, v in kw.items() if k != "stub"})

    monkeypatch.setattr(runner, "start_run", start)
    monkeypatch.setattr(runner, "advance", lambda r: runner.get_run(r) | {"status": "succeeded"})
    monkeypatch.setattr(transfer, "start", lambda a, p: transferred.append((a, p)) or a)
    monkeypatch.setattr(transfer, "matrix", lambda runs: {"runs": runs})
    monkeypatch.setattr(transfer, "markdown", lambda m: f"MATRIX {m['runs']}")
    out = CliRunner().invoke(main.app, ["pack", "run", "realworld"])
    assert out.exit_code == 0, out.output
    req = artifacts.get(started[0]["inputs"]["requirements"]).body
    assert req["kind"] == "brief" and "Settled answers" in req["text"]
    assert started[0]["options"]["profile"] == paths["aspnetcore-realworld-example-app"]
    run_a = transferred[0][0]
    assert transferred == [(run_a, paths["express-bookshelf-realworld-example-app"])]
    assert f"MATRIX [{run_a}, {run_a}]" in out.output
