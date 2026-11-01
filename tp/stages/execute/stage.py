"""Stage 6 — Execute: every gate row on reference and each seeded variant, K attempts over the
network, verdicts as distributions. No LLM. Design: docs/status/execute/001."""

import json
import random
import shutil
from pathlib import Path

from tp.core import db, sut
from tp.core.contract import StageResult, register
from tp.stages.execute import decide, record

K = 3  # main-lane attempts (plan †)
SKIP = ("REJECTED", "UNBOUND")


def _safe(name: str) -> str:
    return name.replace(":", "_").replace("/", "_").replace("+", "_")


def folder(execution_id: int) -> Path:
    return db.data_dir() / "runs" / f"execute-{execution_id}"


class ExecuteStage:
    name = "execute"
    version = "1.0.0"
    consumes: dict[str, int] = {"gate_report": 1, "bound_tests": 1, "env_spec": 1, "run_options": 1}  # noqa: RUF012
    produces: dict[str, int] = {"run_records": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        if ctx.env is None:
            return StageResult.failed("execute needs the env service")
        opts = inputs["run_options"].body
        gate, bound = inputs["gate_report"].body, inputs["bound_tests"].body
        ref_hash = inputs["env_spec"].body.get("spec_hash")
        ex = ctx.review.execution_id
        out, work = folder(ex), folder(ex).with_suffix(".work")
        if (out / record.SEAL).exists():
            return StageResult.failed(f"{out} is already sealed (write-once)")
        rows = {r["node"]: r for r in gate["rows"] if r["status"] not in SKIP}
        k = int(opts.get("k", K))
        variants = opts.get("variants") or ["reference", *(f"bug:{f}" for f in sut.load()["flags"])]
        variants = [variants] if isinstance(variants, str) else variants
        if variants[0] != "reference":
            variants = ["reference", *[v for v in variants if v != "reference"]]  # escalation needs it

        shutil.rmtree(work, ignore_errors=True)
        tests = work / "tests"
        tests.mkdir(parents=True)
        for t in bound["tests"]:
            if t.get("status") == "BOUND":
                (tests / t["file"]).write_text(t["source"])

        run = _Run(ctx, ex, work, rows)
        for variant in variants:
            ctx.emit("execute_variant", variant=variant)
            try:
                spec = ctx.env.up(variant)
            except Exception as e:  # noqa: BLE001 — a dead stack yields NOT_RUN, never verdicts
                run.envs[variant] = {"up_error": f"{type(e).__name__}: {e}"[:300]}
                for a in range(1, k + 1):
                    run.not_run(variant, f"{variant}/a{a}", "main", list(rows), "ENV: bring-up failed")
                continue
            mismatch = variant == "reference" and bool(ref_hash) and spec["spec_hash"] != ref_hash
            seed = random.randrange(10**8)
            run.envs[variant] = {"spec_hash": spec["spec_hash"], "flags": spec["flags"],
                                 "reproduce": spec["reproduce"], "shuffle_seed": seed,
                                 "fingerprint_mismatch": mismatch}  # fmt: skip
            for a in range(1, k + 1):
                env = {"TP_RUN": f"x{ex}.{variant}.a{a}"}
                if a == k and k >= 3:
                    env["TP_SHUFFLE"] = str(seed)  # the order check rides on the last attempt (D4)
                if not run.batch(variant, f"{variant}/a{a}", tests, env, list(rows), "main",
                                 trusted=not mismatch):  # fmt: skip
                    for rest in range(a + 1, k + 1):
                        run.not_run(variant, f"{variant}/a{rest}", "main", list(rows),
                                    "ENV: batch aborted")  # fmt: skip
                    break
            self._escalate(run, variant, bound)
            if variant != "reference":
                run.logs[variant] = ctx.env.logs(variant)
                ctx.env.down(variant)
        run.logs["reference"] = ctx.env.logs("reference")

        verdicts = _verdicts(run.results, rows)
        files = {
            "results.jsonl": "".join(json.dumps(r) + "\n" for r in run.results),
            "probes.json": json.dumps(run.probes, indent=1),
            "manifest.json": json.dumps({"execution": ex, "k": k, "variants": run.envs,
                                         "gate_report": inputs["gate_report"].id,
                                         "bound_tests": inputs["bound_tests"].id}, indent=1),
            **{f"logs/{_safe(v)}.log": text for v, text in run.logs.items()},
        }
        root = record.write(out, files)
        shutil.rmtree(work, ignore_errors=True)
        summary = {v: {} for v in variants}
        for vd in verdicts:
            summary[vd["variant"]][vd["verdict"]] = summary[vd["variant"]].get(vd["verdict"], 0) + 1
        ctx.emit("execute_sealed", root=root, summary=summary)
        return StageResult.succeeded({"run_records": {
            "folder": f"runs/{out.name}", "root": root, "execution": ex, "k": k,
            "variants": run.envs, "summary": summary, "verdicts": verdicts,
            "logs": {v: f"logs/{_safe(v)}.log" for v in run.logs}}})  # fmt: skip

    def _escalate(self, run, variant: str, bound: dict) -> None:
        """Observation lane (D5): INTERMITTENT tests get K=10 here and on reference."""
        main = [r for r in run.results if r["variant"] == variant and r["lane"] == "main"]
        flaky = [vd["node"] for vd in _verdicts(main, run.rows) if vd["verdict"] == "INTERMITTENT"]
        if not flaky:
            return
        files = {n.split("::")[0] for n in flaky}
        obs = run.work / f"obs-{_safe(variant)}"
        obs.mkdir(parents=True, exist_ok=True)
        for t in bound["tests"]:
            if t.get("status") == "BOUND" and t["file"] in files:
                (obs / t["file"]).write_text(t["source"])
        targets = [variant] if variant == "reference" else [variant, "reference"]
        for target in targets:
            for i in range(1, decide.ESCALATE_K + 1):
                tag = f"o{i}" if target == variant else f"o{i}-for-{variant}"
                env = {"TP_RUN": f"x{run.ex}.{target}.{tag}"}
                if not run.batch(target, f"{target}/{tag}", obs, env, flaky, f"obs:{variant}"):
                    break


class _Run:
    def __init__(self, ctx, ex, work, rows):
        self.ctx, self.ex, self.work, self.rows = ctx, ex, work, rows
        self.results: list[dict] = []
        self.probes: list[dict] = []
        self.envs: dict[str, dict] = {}
        self.logs: dict[str, str] = {}

    def _row(self, node, variant, batch, lane, **kw) -> dict:
        case = self.rows[node]["case_id"]
        return {"id": f"ex:{batch}/{case}", "node": node, "case_id": case, "variant": variant,
                "batch": batch, "lane": lane, **kw}  # fmt: skip

    def not_run(self, variant, batch, lane, nodes, reason) -> None:
        self.results += [self._row(n, variant, batch, lane, status="NOT_RUN", reason=reason)
                         for n in nodes]  # fmt: skip

    def _probe(self, variant, batch, when) -> bool:
        probes = self.ctx.env.probe(variant)
        self.probes.append({"id": f"probe:{batch}/{when}", "variant": variant, "batch": batch,
                            "when": when, "probes": probes})  # fmt: skip
        return all(p["ok"] for p in probes)

    def batch(self, variant, batch, tests, env, nodes, lane, trusted=True) -> bool:
        """One pytest batch between two probes (D3). False = abort the rest of this variant."""
        if not self._probe(variant, batch, "pre"):
            self.not_run(variant, batch, lane, nodes, "ENV: pre-probe failed")
            return False
        try:
            res = self.ctx.env.pytest(tests, self.work / "b" / _safe(batch), variant,
                                      mode="network", env=env)  # fmt: skip
            got, error = {r["nodeid"]: r for r in res["results"]}, None
        except Exception as e:  # noqa: BLE001 — runner/batch timeout: nothing from it is trusted
            got, error = {}, f"{type(e).__name__}: {e}"[:300]
        healthy = self._probe(variant, batch, "post")
        for n in nodes:
            r = got.get(n)
            if r is None:
                why = "ENV: post-probe failed" if not healthy else (error or "not collected")
                self.results.append(self._row(n, variant, batch, lane, status="NOT_RUN", reason=why))
                continue
            status = "RAN" if healthy and trusted else "SUSPECT_ENV"
            self.results.append(self._row(n, variant, batch, lane, status=status, **r))
        return healthy and error is None


def _verdicts(results: list[dict], rows: dict) -> list[dict]:
    main: dict[tuple, list] = {}
    obs: dict[tuple, list] = {}
    for r in results:
        if r["lane"] == "main":
            main.setdefault((r["node"], r["variant"]), []).append(r)
        else:
            obs.setdefault((r["node"], r["lane"].removeprefix("obs:"), r["variant"]), []).append(r)
    out = []
    for (node, variant), attempts in main.items():
        g = rows[node]
        vd = {"node": node, "case_id": g["case_id"], "unit": g.get("unit"), "ac": g.get("ac"),
              "variant": variant, "gate_status": g["status"], "lane": decide.lane(g["status"]),
              "seeded": g.get("seeded"), "attempts": [a["id"] for a in attempts],
              **decide.verdict(attempts)}  # fmt: skip
        if vd["verdict"] == "INTERMITTENT":
            here = [r for r in obs.get((node, variant, variant), []) if r["status"] == "RAN"]
            ref = [r for r in obs.get((node, variant, "reference"), []) if r["status"] == "RAN"]
            vd["escalation"] = decide.escalation(here, ref if variant != "reference" else [])
        out.append(vd)
    return out


def _cli():
    import typer

    app = typer.Typer(help="Execute stage tools")

    @app.command("verify-run")
    def verify_run(artifact: str = typer.Argument(help="run_records artifact id")):
        """Recompute every record hash; non-zero exit if anything was changed (X1)."""
        from tp.core import artifacts

        body = artifacts.get(artifact).body
        problems = record.verify(db.data_dir() / body["folder"], body["root"])
        for p in problems:
            typer.echo(f"TAMPERED  {p}")
        typer.echo("intact" if not problems else f"{len(problems)} problem(s)")
        raise typer.Exit(1 if problems else 0)

    return app


STAGE = register(ExecuteStage(), cli=_cli())
