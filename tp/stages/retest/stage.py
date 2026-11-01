"""Stage 9 — Retest: after a fix outside the platform, rerun the same tests on the fixed variant
and prove the defect is gone and nothing else broke. No LLM. Design: docs/status/retest/001."""

import json
from types import SimpleNamespace as NS

from tp.core import artifacts, db
from tp.core.contract import StageResult, register
from tp.stages.execute import record
from tp.stages.execute.stage import ExecuteStage
from tp.stages.retest import decide


class RetestStage:
    name = "retest"
    version = "1.0.0"
    consumes: dict[str, int] = {"run_records": 1, "triage": 1, "bound_tests": 1, "gate_report": 1,  # noqa: RUF012
                                "run_options": 1}  # fmt: skip
    produces: dict[str, int] = {"retest": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        opts = inputs["run_options"].body
        if not opts.get("defect"):
            return StageResult.succeeded({"retest": {"retests": [], "note": "no fix supplied"}})
        after = opts.get("after")
        if not after:
            return StageResult.failed(
                "retest needs --opt after=<variant> (reference or fixed:<patch>)"
            )
        rr, tri = inputs["run_records"].body, inputs["triage"].body
        defect = next((d for d in tri["defects"] if d["id"] == opts["defect"]), None)
        if defect is None:
            known = ", ".join(d["id"] for d in tri["defects"])
            return StageResult.failed(f"no defect {opts['defect']!r} in triage (known: {known})")
        folder = db.data_dir() / rr["folder"]
        if problems := record.verify(folder, rr["root"]):
            return StageResult.failed(f"failing run records were changed: {problems}")
        manifest = json.loads((folder / "manifest.json").read_text())

        before_variant = defect["variant"]
        before = {v["node"]: v for v in rr["verdicts"] if v["variant"] == before_variant}
        nodes = sorted(n for n, v in before.items() if v["case_id"] in defect["cases"])
        changed = decide.same_tests(
            artifacts.get(manifest["bound_tests"]).body, inputs["bound_tests"].body
        )
        out = {"defect": defect["id"], "ac": defect["ac"], "class": defect["class"],
               "fix_kind": decide.fix_kind(before_variant, after),
               "before": {"variant": before_variant, "run_records": inputs["run_records"].id},
               "after": {"variant": after}, "tests_changed": changed,
               "bound_tests": inputs["bound_tests"].id}  # fmt: skip
        if changed or not decide.reproduced(nodes, before):  # nothing to prove: don't run (V1)
            out |= decide.judge(nodes, before, {}, changed, [])
            return StageResult.succeeded({"retest": {"retests": [out]}})

        ctx.emit("retest_running", defect=defect["id"], after=after, k=decide.K)
        res = ExecuteStage().run(ctx, {
            "gate_report": inputs["gate_report"], "bound_tests": inputs["bound_tests"],
            "env_spec": NS(body={}, id=None),  # retest compares fingerprints itself (D4)
            "run_options": NS(body={"variants": [after], "k": decide.K}, id=None)})  # fmt: skip
        if res.status != "succeeded":
            return StageResult.failed(f"executor: {res.reason}")
        run = res.outputs["run_records"]
        after_v = {v["node"]: v for v in run["verdicts"] if v["variant"] == after}
        env = decide.env_diff(
            (rr["variants"].get(before_variant) or {}).get("fingerprint"),
            (run["variants"].get(after) or {}).get("fingerprint"),
        )
        out["after"] |= {"folder": run["folder"], "root": run["root"], "k": run["k"],
                         "summary": run["summary"].get(after, {})}  # fmt: skip
        out["env_diff"] = env
        out |= decide.judge(nodes, before, after_v, changed, env)
        ctx.emit("retest_verdict", defect=defect["id"], verdict=out["verdict"])
        return StageResult.succeeded({"retest": {"retests": [out]}})


STAGE = register(RetestStage())
