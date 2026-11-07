"""Stage 2 — Cases: frozen spec -> ranked abstract test cases with computed expected values,
signed off line by line."""

import json
from pathlib import Path

from tp.core import artifacts, db, review
from tp.core.contract import StageResult, register
from tp.core.hashing import sha256
from tp.stages.cases import ai, build, signals

MAX_ROUNDS = 3  # sign-off rounds before an unresolved rejection leaves the cases unsigned


def _scratch(ctx) -> Path:
    # shortcut: same scratch-file pattern as the spec stage (spec design D11).
    return db.data_dir() / "runs" / f"cases-exec-{ctx.review.execution_id}.json"


def _factories(ctx) -> list[str] | None:
    """The target's precondition names (its data strategy, blackbox D15); None: the default."""
    data = getattr(ctx.strategies, "data", None)
    return data.factories() if hasattr(data, "factories") else None


def _known_preconditions(units: list[dict], factories: list[str] | None) -> list[str]:
    """Preconditions must be factory names (D15); any other one is a situation the binding sets
    up, so it becomes a leading `Given …` step instead of failing the scaffold later. Who the
    caller is stays the binding's call (the data strategy's doc says the default)."""
    notes = []
    for u in units if factories else []:
        unknown = [p for p in u["preconditions"] if p not in factories]
        if unknown:
            u["preconditions"] = [p for p in u["preconditions"] if p in factories]
            u["steps"] = [f"Given: {p.replace('_', ' ')}" for p in unknown] + u["steps"]
            notes.append(f"{u['ac']}: preconditions {unknown} are not factories; moved to steps")
    return notes


def approved_hashes(scope: dict | None) -> set[str]:
    """Line hashes approved in an earlier sign-off for the same SUT ref: carried forward, not
    asked again (D4). scope None (a `fresh` run) carries nothing."""
    out: set[str] = set()
    if scope is None:
        return out
    for item in review.list_(status="resolved"):
        if item["kind"] != "signoff" or not item["detail"] or item["payload"].get("scope") != scope:
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
    version = f"1.2.0+p{ai.version_hash()}"  # 1.1: priority (cases/010); 1.2: decisions (012)
    consumes: dict[str, int] = {"spec": 1, "run_options": 1}  # noqa: RUF012
    produces: dict[str, int] = {"cases": 1, "signoff_md": 1}  # noqa: RUF012
    templates = staticmethod(lambda: {n: t.get("name", n) for n, t in ai.catalogue().items()})
    flows = ("FLOW-items-crud",)  # default scope (design D2); run option `flows` overrides

    def run(self, ctx, inputs):
        path = _scratch(ctx)
        state = json.loads(path.read_text()) if path.exists() else None
        if state is None:
            opts = inputs["run_options"].body
            iteration = artifacts.get(opts["iteration"]).body if opts.get("iteration") else None
            if iteration and iteration["spec"] != inputs["spec"].id:
                return StageResult.failed("iteration was fed from another spec (design 006)")
            # a run focused on routes (M8) tests every flow its spec holds
            flows = opts.get("flows") or (None if opts.get("routes") else self.flows)
            state = self._draft(ctx, inputs["spec"], tuple(flows) if flows else None, iteration,
                                opts.get("templates"), (opts.get("rules") or {}).get("cases"))  # fmt: skip
            state["scope"] = None if opts.get("fresh") else {"sut_ref": state["sut"]["ref"]}
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
        if excluded := {i for i, d in lines.items() if d["decision"] == "exclude"}:
            state["units"] = [u for u in state["units"] if u["id"] not in excluded]
            state["not_tested"] += [{"criterion": i, "reason": "excluded at sign-off"}
                                    for i in sorted(excluded)]  # fmt: skip
        rejected = {i: d["comment"] for i, d in lines.items() if d["decision"] == "reject"}
        if not rejected:
            return self._finish(ctx, state, path, signed=True)
        if state["round"] >= MAX_ROUNDS:
            return self._finish(ctx, state, path, signed=False)
        self._revise(ctx, state, rejected)
        state["round"] += 1
        return self._ask(ctx, state, path, list(rejected))

    # ---- drafting ----------------------------------------------------------------------

    def _draft(self, ctx, spec_art, flows: tuple[str, ...] | None, iteration: dict | None = None,
               templates: list[str] | None = None, rules: list[str] | None = None) -> dict:  # fmt: skip
        """iteration (V4, design 006): scope is its candidates' criteria, whatever their flow,
        and each carries its gaps into the drafting prompt."""
        spec = spec_art.body
        crit = {c["id"]: c for c in spec["criteria"]}
        gaps = {x["criterion"]: x["basis"] for x in (iteration or {}).get("candidates", [])}
        scope, not_tested = [], []
        for c in spec["criteria"]:
            flow = signals.flow_of(c)
            if c["status"] == "out_of_scope":
                not_tested.append({"criterion": c["id"], "reason": "out of scope in the spec"})
            elif iteration and c["id"] not in gaps:
                not_tested.append({"criterion": c["id"], "reason": "not an iteration candidate"})
            elif not iteration and flows is not None and flow not in flows:
                not_tested.append({"criterion": c["id"], "reason": f"flow {flow} not in this run"})
            else:
                scope.append(c | {"flow": flow} | ({"gaps": gaps[c["id"]]} if iteration else {}))
        if iteration or flows is None:
            flows = tuple(sorted({c["flow"] for c in scope}))
        minimum = {c["id"]: [t for t in signals.minimum_templates(c)
                             if templates is None or t in templates] for c in scope}  # fmt: skip
        # the product owner's answers override criterion text that disagrees (cases/012 D1)
        rules = build.decision_rules(spec.get("decisions", [])) + list(rules or [])
        # key by the criterion id the model echoes; it sometimes adds the title ("C-5 Expired …")
        drafts = {
            d["criterion"].split()[0].rstrip(":"): d
            for d in ai.draft(ctx.llm, scope, minimum, templates, rules, _factories(ctx))
        }
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
        units += self._fill_acceptance(ctx, scope, units, minimum, templates, rules, attention)
        attention += _known_preconditions(units, _factories(ctx))
        units, deferred = build.dedupe_and_budget(units, minimum)
        build.rank(units, crit, spec.get("findings", []), self._changed(ctx))
        build.assign_ids(units)
        attention += build.exit_problems(units, scope, minimum, drafts)
        attention += build.decision_conflicts(units, spec.get("decisions", []))
        attention += [f"{c['id']}: criterion is assumed, not confirmed: check its cases"
                      for c in scope if c["status"] == "assumed"]  # fmt: skip
        ctx.emit(
            "cases_drafted", units=len(units), deferred=len(deferred), attention=len(attention)
        )
        return {"spec": spec_art.id, "sut": spec["sut"], "flows": list(flows),
                "criteria": {c["id"]: c for c in scope},
                "minimum": minimum, "drafts": drafts, "units": units, "deferred": deferred,
                "attention": attention, "not_tested": not_tested, "approved": [], "carried": [],
                "round": 1}  # fmt: skip

    @staticmethod
    def _changed(ctx) -> dict[str, list[int]]:
        """The branch's changed lines (env's code scope); none without a live env."""
        scope = getattr(getattr(ctx, "env", None), "code_scope", None)
        try:
            return scope()["changed"] if scope else {}
        except Exception:  # noqa: BLE001 — priority falls back to the other flags
            return {}

    def _fill_acceptance(self, ctx, scope, units, minimum, templates, rules, attention) -> list:
        """Every criterion needs a case for its normal outcome (drafter rule 2). The model
        sometimes skips it; ask once more for just those criteria, keep only acceptance cases."""
        missing = [c for c in scope
                   if c["id"] not in {u["ac"] for u in units if u["technique"] == "acceptance"}]  # fmt: skip
        if not missing:
            return []
        ctx.emit("cases_acceptance_redraft", criteria=len(missing))
        note = ("These criteria have no acceptance case yet. For each one, write exactly one "
                "acceptance case that checks the criterion's `then`, with its exact expected value.")  # fmt: skip
        out = []
        for d in ai.draft(ctx.llm, missing, {c["id"]: [] for c in missing}, templates,
                          [*(rules or []), note], _factories(ctx)):  # fmt: skip
            c = next((x for x in missing if x["id"] == d["criterion"].split()[0].rstrip(":")), None)
            if c is None:
                continue
            only = {**d, "boundaries": [], "tables": [], "skipped_templates": [],
                    "cases": [x for x in d["cases"] if x["technique"] == "acceptance"][:1]}  # fmt: skip
            out += build.units_from_draft(c, c["flow"], only, attention, scope)
        return out

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
        prior = approved_hashes(state.get("scope"))
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
            "scope": state.get("scope"),
        }  # fmt: skip
        return StageResult.awaiting_review([ctx.review.create("signoff", payload)])

    def _finish(self, ctx, state, path, signed: bool) -> StageResult:
        hashes = {build.line(u)["hash"]: u["id"] for u in state["units"]}
        approved = [h for h in hashes if h in set(state["approved"])]
        signed = (
            signed and bool(hashes) and len(approved) == len(hashes)
        )  # nothing to sign != signed
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
