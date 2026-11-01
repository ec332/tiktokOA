"""Stage 4 — Gate: prove each test can fail for the reason it claims before its results count
(plan Stage 4; design docs/status/gate/001)."""

import json
import shutil
import time
from pathlib import Path

import yaml

from tp.core import db
from tp.core.contract import StageResult, register
from tp.core.hashing import sha256
from tp.stages.gate import ai, decide, mutants

HERE = Path(__file__).parent
K = 5  # † stability runs on reference, plus one shuffled run (plan K3)
MUTANT_TIMEOUT = "15"  # † seconds per test under a mutant (a mutant may loop)


def _scratch(ctx) -> Path:
    return db.data_dir() / "runs" / f"gate-exec-{ctx.review.execution_id}.json"


def _node_args(nodes) -> list[str]:
    return ["/work/tests/" + n for n in sorted(nodes)]


class GateStage:
    name = "gate"
    version = f"1.0.0+p{ai.version_hash()}"
    consumes: dict[str, int] = {"bound_tests": 1, "cases": 1, "spec": 1, "run_options": 1}  # noqa: RUF012
    produces: dict[str, int] = {"gate_report": 1, "kill_matrix": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        path = _scratch(ctx)
        if path.exists():
            return self._resume(ctx, json.loads(path.read_text()), path)
        bt, cases, spec = (inputs[t].body for t in ("bound_tests", "cases", "spec"))
        opts = inputs["run_options"].body
        tests = [t for t in bt["tests"] if t["status"] == "BOUND"]
        if not tests:
            return StageResult.failed("no BOUND tests to gate")
        work = db.data_dir() / "runs" / f"gate-exec-{ctx.review.execution_id}"
        shutil.rmtree(work, ignore_errors=True)
        (work / "tests").mkdir(parents=True)
        for t in tests:
            (work / "tests" / t["file"]).write_text(t["source"])
        ctx.env.ensure("reference")
        ctx_info = {"bound_tests": inputs["bound_tests"].id, "cases": inputs["cases"].id,
                    "units": {t["file"]: {"unit": t["unit"], "ac": t["ac"]} for t in tests},
                    "criteria": {c["id"]: {k: c.get(k) for k in ("status", "human_judgement")}
                                 for c in spec["criteria"]},
                    "signed": bool(cases.get("signoff", {}).get("signed")),
                    "refs": decide.code_refs(spec)}  # fmt: skip
        if opts.get("admit", "v2") == "v1":
            return self._v1(ctx, work, ctx_info)
        m = self._measure(ctx, work, tests, ctx_info)
        state = self._assess(ctx_info, m)
        state["proposals"] = ai.propose(ctx.llm, state["survivors"])
        items = [ctx.review.create("equivalent_mutant", p | {"diff": next(
                     s["diff"] for s in state["survivors"] if s["mutant"] == p["mutant"])})
                 for p in state["proposals"] if p["equivalent"]]  # fmt: skip
        if not items:
            return self._finish(ctx, state, work, path, set())
        path.write_text(json.dumps(state))
        return StageResult.awaiting_review(items)

    # ---- K2: design iteration v1 --------------------------------------------------------

    def _v1(self, ctx, work, info):
        """Admit on "passes on reference" only (plan K2): one run, no stability, no routing."""
        r = ctx.env.pytest(work / "tests", work / "v1", "reference")
        rows = [{"node": x["nodeid"], **self._where(info, x["nodeid"]), "case_id": x["case_id"],
                 "outcome": x["outcome"], "origin": x["origin"],
                 "status": "ADMITTED" if x["outcome"] == "passed" else "REJECTED"}
                for x in r["results"]]  # fmt: skip
        counts = dict(sorted({s: sum(1 for x in rows if x["status"] == s)
                              for s in {x["status"] for x in rows}}.items()))  # fmt: skip
        ctx.emit("gate_v1", **counts)
        report = {"admit": "v1", "bound_tests": info["bound_tests"], "rows": rows,
                  "summary": counts}  # fmt: skip
        return StageResult.succeeded({"gate_report": report, "kill_matrix": {
            "note": "v1 admits on one passing run; no mutants are run", "mutants": [],
            "rows": [], "cells": {}, "csv": ""}})  # fmt: skip

    @staticmethod
    def _where(info, node: str) -> dict:
        return info["units"].get(node.split("::")[0], {"unit": None, "ac": None})

    # ---- measurement (cached, K7) -------------------------------------------------------

    def _measure(self, ctx, work, tests, info) -> dict:
        scope = ctx.env.code_scope()
        seeded = yaml.safe_load((HERE / "seeded.yaml").read_text())
        key = sha256({"tests": sorted((t["file"], t["source"], t["lock_hash"]) for t in tests),
                      "runner": json.loads((db.data_dir() / "published.json").read_text())
                      ["runner"]["digest"],
                      "scope": {p: (Path(scope["src"]) / p).read_text() for p in scope["mutate"]},
                      "version": self.version, "k": K, "seeded": seeded,
                      "code": {p.name: p.read_text() for p in sorted(HERE.glob("*.py"))}})  # fmt: skip
        cache = db.data_dir() / "gate_cache" / f"{key}.json"
        if cache.exists():
            ctx.emit("gate_cache", hit=True, key=key[:12])
            return json.loads(cache.read_text()) | {"cache": "hit"}
        t0, timings = time.perf_counter(), {}
        seed = info["bound_tests"].removeprefix("sha256:")[:8]

        runs = [ctx.env.pytest(work / "tests", work / f"k{i}", "reference")["results"]
                for i in range(K)]  # fmt: skip
        runs.append(ctx.env.pytest(work / "tests", work / "shuffled", "reference",
                                   env={"TP_SHUFFLE": seed})["results"])  # fmt: skip
        stab = decide.stability(runs)
        passing = sorted(n for n, s in stab.items() if s["stable"] and s["outcome"] == "passed")
        timings["stability"] = round(time.perf_counter() - t0, 1)
        ctx.emit("gate_stability", nodes=len(stab), stable_passing=len(passing))

        seeded_res = self._seeded(ctx, work, info, seeded, passing)
        timings["seeded"] = round(time.perf_counter() - t0 - sum(timings.values()), 1)

        files = mutants.generate(ctx.env, scope["mutate"], work / "mutants")
        ms = mutants.parse(work / "mutants", scope["mutate"])
        stats = ctx.env.pytest(work / "tests", work / "stats", "reference", mounts=files,
                               env={"MUTANT_UNDER_TEST": "stats"},
                               args=tuple(_node_args(passing)))["results"]  # fmt: skip
        reached = {r["nodeid"]: r.get("reached", []) for r in stats if r["outcome"] == "passed"}
        broken = sorted(set(passing) - set(reached))  # fail with the trampolines in place
        linked = {m["name"]: sorted(n for n, fns in reached.items() if m["function"] in fns)
                  for m in ms}  # fmt: skip
        jobs = [{"key": m["name"], "env": {"MUTANT_UNDER_TEST": m["name"],
                                           "TP_TIMEOUT": MUTANT_TIMEOUT},
                 "args": _node_args(linked[m["name"]])} for m in ms if linked[m["name"]]]  # fmt: skip
        matrix = ctx.env.pytest(work / "tests", work / "matrix", "reference", mounts=files,
                                matrix=jobs)["matrix"] if jobs else {}  # fmt: skip
        timings["mutants"] = round(time.perf_counter() - t0 - sum(timings.values()), 1)
        ctx.emit("gate_matrix", mutants=len(ms), jobs=len(jobs), seconds=timings["mutants"])
        out = {"stability": stab, "shuffle_seed": seed, "seeded": seeded_res, "mutants": ms,
               "linked": linked, "matrix": {k: [{f: r[f] for f in ("nodeid", "outcome", "origin")}
                                                for r in v] for k, v in matrix.items()},
               "overlay_broken": broken, "timings": timings}  # fmt: skip
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
        return out | {"cache": "miss"}

    def _seeded(self, ctx, work, info, seeded, passing) -> dict:
        """K4: each test of a targeted criterion must fail on its flag's variant: in-process
        first, then confirmed once over the network on that variant's stack (design D6)."""
        res = {}
        for flag, spec in seeded.items():
            nodes = [n for n in passing if self._where(info, n)["ac"] in spec["criteria"]]
            if not nodes:
                continue
            variant = f"bug:{flag}"
            inproc = ctx.env.pytest(work / "tests", work / f"seed-{flag}", variant,
                                    args=tuple(_node_args(nodes)))["results"]  # fmt: skip
            ctx.env.ensure(variant)
            try:
                net = ctx.env.pytest(work / "tests", work / f"seed-{flag}-net", variant,
                                     mode="network", args=tuple(_node_args(nodes)))["results"]  # fmt: skip
            finally:
                ctx.env.down(variant)
            fail = lambda recs: {
                r["nodeid"]: r["origin"]
                for r in recs
                if r["outcome"] == "failed" and r["origin"] in decide.KILL
            }
            a, b = fail(inproc), fail(net)
            res[flag] = {"criteria": spec["criteria"], "nodes": {
                n: {"in_process": a.get(n), "network": b.get(n), "killed": n in a and n in b}
                for n in nodes}}  # fmt: skip
        return res

    # ---- decisions ----------------------------------------------------------------------

    def _assess(self, info, m) -> dict:
        unit_of = {n: self._where(info, n)["unit"] for n in m["stability"]}
        cells = decide.kills(m["matrix"])
        seeded_of = {n: (flag, v) for flag, s in m["seeded"].items() for n, v in s["nodes"].items()}
        targeted = {c for s in m["seeded"].values() for c in s["criteria"]}
        rows = []
        for node, st in sorted(m["stability"].items()):
            where = self._where(info, node)
            crit = info["criteria"].get(where["ac"], {})
            status = decide.route(st, crit, info["signed"])
            flag, seed = seeded_of.get(node, (None, None))
            n_kills = n_unique = 0
            if status is None:
                status, n_kills, n_unique = decide.admit(
                    node, unit_of, cells, bool(seed and seed["killed"]),
                    measurable=bool(m["mutants"]) or where["ac"] in targeted)  # fmt: skip
            rows.append({"node": node, **where, "case_id": st["case_id"],
                         "stability": f"STABLE@K{K}" if st["stable"] else "FLAKY",
                         "outcome": st["outcome"], "origin": st["origin"], "status": status,
                         "seeded": seed and {"flag": flag, **seed},
                         "misses_target": bool(seed) and not seed["killed"],
                         "kills": n_kills, "unique_kills": n_unique,
                         "linked_mutants": sum(1 for v in m["linked"].values() if node in v)})  # fmt: skip
        return {"info": info, "rows": rows, "cells": cells, "measure": {
                    k: m[k] for k in ("mutants", "linked", "timings", "shuffle_seed",
                                      "overlay_broken", "cache")},
                "survivors": decide.survivors(m["mutants"], cells, m["linked"], info["refs"])}  # fmt: skip

    def _resume(self, ctx, state, path):
        items = ctx.review.items()
        if open_ := [i["id"] for i in items if i["status"] == "open"]:
            return StageResult.awaiting_review(open_)
        work = db.data_dir() / "runs" / f"gate-exec-{ctx.review.execution_id}"
        eq = {i["payload"]["mutant"] for i in items if i["decision"] == "equivalent"}
        return self._finish(ctx, state, work, path, eq)

    def _finish(self, ctx, state, work, path, equivalent: set[str]):
        path.unlink(missing_ok=True)
        rows, cells, meas = state["rows"], state["cells"], state["measure"]
        names = [m["name"] for m in meas["mutants"]]
        killed = {m for row in cells.values() for m, c in row.items() if c == "K"}
        by_crit: dict[str, dict] = {}
        for r in rows:
            c = by_crit.setdefault(r["ac"], {"statuses": {}, "nodes": set()})
            c["statuses"][r["status"]] = c["statuses"].get(r["status"], 0) + 1
            c["nodes"].add(r["node"])
        criteria = {}
        for ac, c in sorted(by_crit.items()):
            reach = {m for m, ns in meas["linked"].items() if c["nodes"] & set(ns)}
            mine = {m for n in c["nodes"] for m, v in cells.get(n, {}).items() if v == "K"}
            criteria[ac] = {"statuses": c["statuses"], "reached_mutants": len(reach),
                            "killed": len(mine), "equivalent": len(reach & equivalent),
                            "strength": decide.strength(len(mine), len(reach),
                                                        len(reach & equivalent))}  # fmt: skip
        surv = [s | {"equivalent": s["mutant"] in equivalent} for s in state["survivors"]]
        summary: dict[str, int] = {}
        for r in rows:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        report = {"admit": "v2", **{k: state["info"][k] for k in ("bound_tests", "cases")},
                  "rows": rows, "summary": dict(sorted(summary.items())), "criteria": criteria,
                  "mutants": {"total": len(names), "killed": len(killed),
                              "equivalent": len(equivalent),
                              "not_covered": sum(1 for s in surv if not s["covered"]),
                              "strength": decide.strength(len(killed), len(names),
                                                          len(equivalent))},
                  "survivors": surv, "proposals": state.get("proposals", []),
                  "overlay_broken": meas["overlay_broken"], "timings": meas["timings"],
                  "shuffle_seed": meas["shuffle_seed"], "cache": meas["cache"]}  # fmt: skip
        order = [r["node"] for r in sorted(rows, key=lambda r: (r["ac"] or "", r["node"]))]
        csv_text = decide.matrix_csv(order, names, cells)
        work.mkdir(parents=True, exist_ok=True)
        (work / "kill_matrix.csv").write_text(csv_text)
        ctx.emit("gate_done", **report["summary"])
        km = {"mutants": [{k: m[k] for k in ("name", "file", "line", "function")}
                          for m in meas["mutants"]],
              "rows": [{"node": r["node"], "ac": r["ac"], "status": r["status"]}
                       for r in sorted(rows, key=lambda r: (r["ac"] or "", r["node"]))],
              "cells": cells, "csv": csv_text, "csv_path": str(work / "kill_matrix.csv")}  # fmt: skip
        return StageResult.succeeded({"gate_report": report, "kill_matrix": km})


STAGE = register(GateStage())
