"""Stage 3 — Generate: signed cases -> one pytest file per case unit whose only model-written
part is the binding (plan Stage 3; design docs/status/generate/001)."""

import json
from pathlib import Path

from tp.core import db
from tp.core.contract import StageResult, register
from tp.core.hashing import sha256
from tp.stages.generate import ai, image, loop
from tp.stages.generate.cli import app as cli


def _scratch(ctx) -> Path:
    return db.data_dir() / "runs" / f"generate-exec-{ctx.review.execution_id}.json"


def ensure_runner(ctx) -> None:
    """The runner image holds a copy of the SUT code (in-process tests, mutants), so it must be
    built on the current backend image. A new target branch or patch set changes that image;
    rebuild the runner then instead of failing (M8)."""
    reach = ctx.strategies.reach
    base = reach.image("reference")
    if base is None:  # black-box target: the generic runner, tagged by its wheel's hash, so an
        image.build_blackbox()  # unchanged wheel is a Docker-cached no-op
        return
    try:
        current = image.published().get("base")
    except image.ImageError:
        current = None
    if current != base:
        ctx.emit("runner_rebuilding", base=base, was=current)
        image.build(reach)
        ctx.emit("runner_rebuilt", base=base)


def _runner(ctx) -> dict:
    no_image = ctx.strategies.reach.image("reference") is None
    return image.published_blackbox() if no_image else image.published()


class GenerateStage:
    name = "generate"
    version = f"1.0.0+p{ai.version_hash()}"
    consumes: dict[str, int] = {"cases": 1, "spec": 1, "run_options": 1}  # noqa: RUF012
    produces: dict[str, int] = {"bound_tests": 1, "ledger": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        path = _scratch(ctx)
        if path.exists():
            return self._resume(ctx, json.loads(path.read_text()), path)
        cases, spec = inputs["cases"].body, inputs["spec"].body
        opts = inputs["run_options"].body
        # unattended runs go on with unsigned cases; the `unsigned` label follows every output
        if not cases.get("signoff", {}).get("signed") and not opts.get("unattended"):
            return StageResult.failed("cases are not signed: generate consumes signed cases only")
        guards, variant = opts.get("guards", "v2"), opts.get("variant", "reference")
        if guards not in loop.REPAIRABLE:
            return StageResult.failed(f"unknown guards {guards!r}: use v1 or v2")
        units = [u for u in cases["cases"] if not opts.get("units") or u["id"] in opts["units"]]
        ctx.strategies.reach.ensure("reference")
        ensure_runner(ctx)
        openapi = ctx.strategies.spec.openapi("reference")
        workdir = db.data_dir() / "runs" / f"generate-exec-{ctx.review.execution_id}"
        criteria = {c["id"]: c for c in spec["criteria"]}
        tests, ledger = loop.bind_all(ctx, units, criteria, openapi, workdir, guards, variant,
                                      int(opts.get("max_repairs", loop.MAX_REPAIRS)))  # fmt: skip
        state = {"cases": inputs["cases"].id, "signature": cases["signoff"]["hash"],
                 "guards": guards, "variant": variant, "runner": _runner(ctx),
                 "openapi_hash": sha256(openapi), "tests": [tests[u["id"]] for u in units],
                 "ledger": ledger}  # fmt: skip
        items = [ctx.review.create("unbound", {"unit": t["unit"], "reason": t["reason"],
                                               "failures": t.get("failures", [])[:3],
                                               "source": t.get("source")})
                 for t in state["tests"] if t["status"] == "UNBOUND"]  # fmt: skip
        if not items:
            return self._finish(ctx, state, path)
        path.write_text(json.dumps(state))
        return StageResult.awaiting_review(items)

    def _resume(self, ctx, state, path):
        items = ctx.review.items()
        if open_ := [i["id"] for i in items if i["status"] == "open"]:
            return StageResult.awaiting_review(open_)
        decided = {i["payload"]["unit"]: i["decision"] for i in items}
        for t in state["tests"]:
            if t["unit"] in decided:
                t["review"] = decided[t["unit"]]
        return self._finish(ctx, state, path)

    def _finish(self, ctx, state, path):
        path.unlink(missing_ok=True)
        counts: dict[str, int] = {}
        for t in state["tests"]:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        ctx.emit("tests_bound", **counts)
        ledger = state.pop("ledger")
        body = state | {"summary": counts}
        return StageResult.succeeded({"bound_tests": body, "ledger": {"attempts": ledger}})


STAGE = register(GenerateStage(), cli=cli)
