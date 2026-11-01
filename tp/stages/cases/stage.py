"""Stage 2 — Cases: frozen spec -> ranked abstract test cases with computed expected values,
signed off line by line."""

import json
from pathlib import Path

from tp.core import db, review
from tp.core.contract import StageResult, register
from tp.core.hashing import sha256
from tp.stages.cases import ai, build, signals

MAX_ROUNDS = 3  # sign-off rounds before an unresolved rejection leaves the cases unsigned


def _scratch(ctx) -> Path:
    # shortcut: same scratch-file pattern as the spec stage (spec design D11).
    return db.data_dir() / "runs" / f"cases-exec-{ctx.review.execution_id}.json"


def approved_hashes() -> set[str]:
    """Line hashes approved in any earlier sign-off: carried forward, not asked again (D4)."""
    out = set()
    for item in review.list_(status="resolved"):
        if item["kind"] != "signoff" or not item["detail"]:
            continue
        for ln in item["payload"].get("lines", []):
            if (
                ln.get("hash")
                and item["detail"]["lines"].get(ln["id"], {}).get("decision") == "approve"
            ):
                out.add(ln["hash"])
    return out


class CasesStage:
    name = "cases"
    version = f"1.0.0+p{ai.version_hash()}"
    consumes: dict[str, int] = {"spec": 1}  # noqa: RUF012 — contract shape
    produces: dict[str, int] = {"cases": 1, "signoff_md": 1}  # noqa: RUF012
    flows = ("FLOW-items-crud",)  # design D2: the thin slice for M2

    def run(self, ctx, inputs):
        path = _scratch(ctx)
        state = json.loads(path.read_text()) if path.exists() else None
        if state is None:
            state = self._draft(ctx, inputs["spec"])
            return self._ask(ctx, state, path, [u["id"] for u in state["units"]])
        items = ctx.review.items()
        if open_ := [i["id"] for i in items if i["status"] == "open"]:
            return StageResult.awaiting_review(open_)
        last = items[-1]
        if last["decision"] == "defer":
            return self._finish(ctx, state, path, signed=False)
        lines = last["detail"]["lines"]
        for ln in last["payload"]["lines"]:
            if lines[ln["id"]]["decision"] == "approve":
                state["approved"].append(ln["hash"])
        rejected = {i: d["comment"] for i, d in lines.items() if d["decision"] == "reject"}
        if not rejected:
            return self._finish(ctx, state, path, signed=True)
        if state["round"] >= MAX_ROUNDS:
            return self._finish(ctx, state, path, signed=False)
        self._revise(ctx, state, rejected)
        state["round"] += 1
        return self._ask(ctx, state, path, list(rejected))

    # ---- drafting ----------------------------------------------------------------------

    def _draft(self, ctx, spec_art) -> dict:
        spec = spec_art.body
        crit = {c["id"]: c for c in spec["criteria"]}
        scope, not_tested = [], []
        for c in spec["criteria"]:
            flow = signals.flow_of(c)
            if c["status"] == "out_of_scope":
                not_tested.append({"criterion": c["id"], "reason": "out of scope in the spec"})
            elif flow not in self.flows:
                not_tested.append({"criterion": c["id"], "reason": f"flow {flow} not in this run"})
            else:
                scope.append(c | {"flow": flow})
        minimum = {c["id"]: signals.minimum_templates(c) for c in scope}
        drafts = {d["criterion"]: d for d in ai.draft(ctx.llm, scope, minimum)}
        attention: list[str] = []
        units = []
        for c in scope:
            if c["id"] not in drafts:
                attention.append(f"{c['id']}: the drafter returned no cases")
                continue
            units += build.units_from_draft(c, c["flow"], drafts[c["id"]], attention, scope)
            for s in drafts[c["id"]]["skipped_templates"]:
                attention.append(
                    f"{c['id']}: skipped minimum template {s['template']}: {s['reason']}"
                )
        units, deferred = build.dedupe_and_budget(units, minimum)
        build.rank(units, crit, spec.get("findings", []))
        build.assign_ids(units)
        attention += build.exit_problems(units, scope, minimum, drafts)
        attention += [f"{c['id']}: criterion is assumed, not confirmed: check its cases"
                      for c in scope if c["status"] == "assumed"]  # fmt: skip
        ctx.emit(
            "cases_drafted", units=len(units), deferred=len(deferred), attention=len(attention)
        )
        return {"spec": spec_art.id, "sut": spec["sut"], "flows": list(self.flows),
                "criteria": {c["id"]: c for c in scope},
                "minimum": minimum, "drafts": drafts, "units": units, "deferred": deferred,
                "attention": attention, "not_tested": not_tested, "approved": [], "carried": [],
                "round": 1}  # fmt: skip

    def _revise(self, ctx, state, rejected: dict[str, str]) -> None:
        """Rewrite the rejected units using the reviewer's comments; same shape as before."""
        by_id = {u["id"]: u for u in state["units"]}
        revised = ai.revise(ctx.llm, [{"id": i, "case": by_id[i], "comment": c}
                                      for i, c in rejected.items() if i in by_id])  # fmt: skip
        for kind, items in (("case", revised["cases"]), ("boundary", revised["boundaries"]),
                            ("table", revised["tables"])):  # fmt: skip
            for r in items:
                old = by_id.get(r["id"])
                if old is None:
                    continue
                c = state["criteria"][old["ac"]]
                siblings = list(state["criteria"].values())
                u = build.build_unit(c, old["flow"], kind, r, state["attention"], siblings)
                if u is None:
                    continue
                u |= {"score": old["score"], "priority": old["priority"], "quality": "revised"}
                u["id"] = old["id"]
                build.assign_point_ids(u)
                state["units"][state["units"].index(old)] = u

    # ---- sign-off ----------------------------------------------------------------------

    def _ask(self, ctx, state, path, ids: list[str]) -> StageResult:
        prior = approved_hashes()
        lines = []
        for u in state["units"]:
            if u["id"] not in ids:
                continue
            ln = build.line(u)
            if ln["hash"] in prior:  # approved before, unchanged: carried forward
                state["approved"].append(ln["hash"])
                state["carried"].append(u["id"])
            else:
                lines.append(ln)
        path.write_text(json.dumps(state))
        if not lines:
            return self._finish(ctx, state, path, signed=True)
        payload = {
            "lines": lines,
            "attention": state["attention"] if state["round"] == 1 else [],
            "worked_examples": [{"id": u["id"], "field": u["boundary"]["field"], "points": u["points"]}
                                for u in state["units"] if "points" in u and u["id"] in ids],
            "not_tested": state["not_tested"] if state["round"] == 1 else [],
            "round": state["round"],
        }  # fmt: skip
        return StageResult.awaiting_review([ctx.review.create("signoff", payload)])

    def _finish(self, ctx, state, path, signed: bool) -> StageResult:
        hashes = {build.line(u)["hash"]: u["id"] for u in state["units"]}
        approved = [h for h in hashes if h in set(state["approved"])]
        signed = signed and len(approved) == len(hashes)
        signoff = {"signed": signed, "hash": sha256(sorted(approved)) if signed else None,
                   "lines": len(hashes), "approved": len(approved),
                   "carried_forward": sorted(state["carried"])}  # fmt: skip
        body = {k: state[k] for k in ("spec", "sut", "flows", "minimum", "deferred", "attention",
                                      "not_tested")}  # fmt: skip
        body |= {"cases": [{k: v for k, v in u.items() if k != "score"} for u in state["units"]],
                 "signoff": signoff}  # fmt: skip
        path.unlink(missing_ok=True)
        ctx.emit("cases_signed" if signed else "cases_unsigned", hash=signoff["hash"],
                 lines=signoff["lines"], approved=signoff["approved"])  # fmt: skip
        return StageResult.succeeded({"cases": body, "signoff_md": {"markdown": render(body)}},
                                     labels=[] if signed else ["unsigned"])  # fmt: skip


def render(body: dict) -> str:
    s = body["signoff"]
    out = [f"# Test cases — sign-off ({'signed ' + s['hash'][:12] if s['signed'] else 'UNSIGNED'})",
           "", (f"Flows: {', '.join(body['flows'])}. {s['approved']}/{s['lines']} lines approved, "
           f"{len(s['carried_forward'])} carried forward from an earlier sign-off."), "",
           "## Attention", ""]  # fmt: skip
    out += [f"- {a}" for a in body["attention"]] or ["- none"]
    out += ["", "## Worked examples (boundaries)", ""]
    for u in body["cases"]:
        if "points" in u:
            pts = " | ".join(f"{p['value']} → {p['expected']['value']}" for p in u["points"])
            out.append(f"- {u['id']} `{u['boundary']['field']}` limit {u['boundary']['limit']} "
                       f"({'inclusive' if u['boundary']['inclusive'] else 'exclusive'}): {pts}")  # fmt: skip
    for flow in body["flows"]:
        out += ["", f"## {flow}", "", "| id | prio | technique | purpose | expected | catches |",
                "|---|---|---|---|---|---|"]  # fmt: skip
        for u in body["cases"]:
            if u["flow"] != flow:
                continue
            exp = (f"{len(u['rows'])} rows" if "rows" in u else "see worked example"
                   if "points" in u else f"{u['expected']['value']} ({u['expected']['oracle']})")  # fmt: skip
            out.append(f"| {u['id']} | {u['priority']} | {u['technique']} | {u['purpose']} | {exp} "
                       f"| {u['defect_hypothesis']} |")  # fmt: skip
    out += ["", "## Not tested", ""]
    out += [f"- {n['criterion']}: {n['reason']}" for n in body["not_tested"]]
    out += [f"- {d['ac']} ({d['technique']}): deferred, {d['deferred']}" for d in body["deferred"]]
    return "\n".join(out) + "\n"


STAGE = register(CasesStage())
