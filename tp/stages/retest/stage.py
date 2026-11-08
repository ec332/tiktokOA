"""Stage 9 — Retest: after a fix outside the platform, rerun the same tests on the fixed variant
and prove the defect is gone and nothing else broke. No LLM. Design: docs/status/retest/001."""

import json
from types import SimpleNamespace as NS

from tp.core import artifacts, db
from tp.core.contract import StageResult, register
from tp.stages.execute import record
from tp.stages.execute.stage import ExecuteStage
from tp.stages.retest import decide, feed


class RetestStage:
    name = "retest"
    version = "1.2.0"  # 1.1: auto mode (autonomy/001 F3); 1.2: one executor call for all fixes
    consumes: dict[str, int] = {"run_records": 1, "triage": 1, "bound_tests": 1, "gate_report": 1,  # noqa: RUF012
                                "run_options": 1}  # fmt: skip
    produces: dict[str, int] = {"retest": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        opts, tri = inputs["run_options"].body, inputs["triage"].body
        if not opts.get("defect"):
            if opts.get("stops") is None and not opts.get("unattended"):
                return StageResult.succeeded({"retest": {"retests": [], "note": "no fix supplied"}})
            return self._auto(ctx, inputs, tri)
        after = opts.get("after")
        if not after:
            return StageResult.failed(
                "retest needs --opt after=<variant> (reference or fixed:<patch>)"
            )
        defect = next((d for d in tri["defects"] if d["id"] == opts["defect"]), None)
        if defect is None:
            known = ", ".join(d["id"] for d in tri["defects"])
            return StageResult.failed(f"no defect {opts['defect']!r} in triage (known: {known})")
        out = self._one(ctx, inputs, defect, after)
        if isinstance(out, StageResult):
            return out
        return StageResult.succeeded({"retest": {"retests": [out]}})

    def _auto(self, ctx, inputs, tri):
        """No click (autonomy/001 F3): every real product defect whose suggested fix applies is
        retested on fixed:<that patch>, with the same tests."""
        todo = []
        for d in tri["defects"]:
            if d["kind"] != "real" or d["class"] != "PRODUCT_DEFECT":
                continue
            if not (d.get("suggestion") or {}).get("applies"):
                continue
            fix = artifacts.put("fix_patch", {"patch": d["suggestion"]["patch"], "defect": d["id"],
                                              "triage": inputs["triage"].id})  # fmt: skip
            todo.append((d, fix.id))
        # one executor call for every fix: an execution's run folder is write-once
        run = self._execute(ctx, inputs, [f"fixed:{f}" for _, f in todo]) if todo else None
        if isinstance(run, StageResult):
            return run
        outs = []
        for d, f in todo:
            out = self._one(ctx, inputs, d, f"fixed:{f}", run)
            if isinstance(out, StageResult):
                return out
            outs.append(out | {"fix_patch": f})
        note = {} if outs else {"note": "no real product defect with a suggested fix that applies"}
        return StageResult.succeeded({"retest": {"retests": outs, "auto": True, **note}})

    def _execute(self, ctx, inputs, variants: list[str]):
        res = ExecuteStage().run(ctx, {
            "gate_report": inputs["gate_report"], "bound_tests": inputs["bound_tests"],
            "env_spec": NS(body={}, id=None),  # retest compares fingerprints itself (D4)
            "run_options": NS(body={"variants": variants, "k": decide.K}, id=None)})  # fmt: skip
        if res.status != "succeeded":
            return StageResult.failed(f"executor: {res.reason}")
        return res.outputs["run_records"]

    def _one(self, ctx, inputs, defect: dict, after: str, run: dict | None = None):
        """One defect's retest record, or a failed StageResult."""
        rr = inputs["run_records"].body
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
            return out | decide.judge(nodes, before, {}, changed, [])

        ctx.emit("retest_running", defect=defect["id"], after=after, k=decide.K)
        if run is None:
            run = self._execute(ctx, inputs, [after])
            if isinstance(run, StageResult):
                return run
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
        return out


STAGE = register(RetestStage(), cli=feed.app)
