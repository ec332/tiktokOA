"""Stage 1 — Spec: requirements (story or brief) + code -> frozen, sourced criteria."""

import json
from pathlib import Path

from tp.core import db, review, sut
from tp.core.contract import StageResult, register
from tp.stages.spec import ai, checks, code_index, questions
from tp.stages.spec.cli import app as cli
from tp.stages.spec.render import render


def load_index(root: Path = Path(".")) -> tuple[dict, dict[str, str]]:
    cfg = sut.load(root)
    files = code_index.source_files(
        sut.checkout(cfg, root), cfg["ref"], cfg.get("code_scope", {}).get("exclude", [])
    )
    idx = code_index.build(files, cfg["surfaces"]["api"]["base"])
    idx["sut"] = {"name": cfg["name"], "ref": cfg["ref"]}
    return idx, files


def _rules(opts: dict) -> list[str]:
    return list((opts.get("rules") or {}).get("clarify", []))


def suggest_rules(llm, overview: str, routes: list[str]) -> list[dict]:
    """For the New run screen (M8 D14): suggestions from the overview and the chosen routes."""
    index, files = load_index()
    changed = list((sut.load().get("target") or {}).get("changed", {}))
    if routes:
        index, files = code_index.focus(index, files, routes, changed)
    return ai.suggest_rules(llm, overview, routes, code_index.numbered_source(files))


def routes() -> list[dict]:
    """Every API route of the target, for the route chips: [{method, path, file, line}]."""
    index, _ = load_index()
    return [{k: r[k] for k in ("method", "path", "file", "line")} for r in index["routes"]]


def _scratch(ctx) -> Path:
    # shortcut: draft kept outside the artifact store between review rounds (design D11);
    # replace with a contract-level scratch store if a second stage needs one.
    return db.data_dir() / "runs" / f"spec-exec-{ctx.review.execution_id}.json"


class SpecStage:
    name = "spec"
    version = f"1.0.0+p{ai.prompt_hash()}"  # prompt changes bump the version (design D8)
    consumes: dict[str, int] = {"requirements": 1, "run_options": 1}  # noqa: RUF012
    produces: dict[str, int] = {"spec": 1, "spec_md": 1}  # noqa: RUF012
    # for the New run screen (M8), reached through the stage registry
    routes = staticmethod(routes)
    suggest_rules = staticmethod(suggest_rules)
    default_rules = staticmethod(ai.default_rules)

    def run(self, ctx, inputs):
        path = _scratch(ctx)
        state = json.loads(path.read_text()) if path.exists() else None
        if state is None:
            opts = inputs["run_options"].body
            state = self._round1(ctx, inputs["requirements"], opts)
            # Earlier answers are reused only for the same SUT ref, requirements text, routes and
            # rules (M8 D15): new rules can change what should be asked.
            scope = {"sut_ref": state["sut"]["ref"], "requirements": state["requirements"]}
            if opts.get("routes") or _rules(opts):
                scope |= {"routes": sorted(opts.get("routes", [])), "rules": _rules(opts)}
            state["scope"] = None if opts.get("fresh", False) else scope
            return self._ask(ctx, state, path)
        items = ctx.review.items()
        if open_ := [i["id"] for i in items if i["status"] == "open"]:
            return StageResult.awaiting_review(open_)
        if state["pending"]:
            self._integrate(state, [i for i in items if i["id"] in state["pending"]])
            if state["followups"] and state["round"] < questions.ROUNDS:
                state["round"] += 1
                cands, state["followups"] = state["followups"], []
                return self._ask(ctx, state, path, cands)
        return self._finish(ctx, state, path)

    # ---- round 1: extract, check, lint, investigate ------------------------------

    def _round1(self, ctx, req, opts) -> dict:
        kind, text = req.body["kind"], req.body["text"]
        index, files = load_index()
        if opts.get("routes"):  # M8 D12: the chosen routes, their app imports, the branch diff
            changed = list((sut.load().get("target") or {}).get("changed", {}))
            index, files = code_index.focus(index, files, opts["routes"], changed)
            ctx.emit("spec_focused", routes=len(index["routes"]), files=len(files))
        extracted, rejected = checks.check_extracted(ai.extract(ctx.llm, kind, text), text)
        criteria = [{"id": f"C-{n}", **c} for n, c in enumerate(extracted, 1)]
        checks.lint_all(criteria)
        for c in criteria:
            c.update(status=checks.initial_status(c), suspected_defect=False, review_needed=False)
        flagged = {c["id"]: c["ambiguity"] for c in criteria if c["ambiguity"]}
        source = code_index.numbered_source(files)
        found = ai.investigate(ctx.llm, kind, text, criteria, index, source, flagged, _rules(opts))
        ctx.emit(
            "spec_investigated", findings=len(found["findings"]), observed=len(found["observed"])
        )

        known = {code_index.route_key(r["method"], r["path"]): f"{r['method']} {r['path']}"
                 for r in index["routes"]}  # fmt: skip
        by_id = {c["id"]: c for c in criteria}
        for cr in found["criterion_routes"]:
            if c := by_id.get(cr["criterion"]):
                hits = [known[k] for r in cr["routes"] if " " in r
                        and (k := code_index.route_key(*r.split(" ", 1))) in known]  # fmt: skip
                c["routes"] = sorted(set(c["routes"]) | set(hits))
        observed, rej_obs = checks.check_observed(found["observed"], index["files"])
        findings, rej_find = [], []
        for f in found["findings"]:
            ok = all(e["file"] in index["files"] and 1 <= e["line"] <= index["files"][e["file"]]
                     for e in f["evidence"])  # fmt: skip
            (findings if ok and f["evidence"] else rej_find).append(f)
        observed += questions.defects_from_findings(findings, observed)
        for n, o in enumerate(observed, 1):
            o.update(id=f"O-{n}", status="observed", review_needed=False)
            o["ambiguity"] = checks.lint(o)
            if o.get("linked_criteria"):  # inherit the routes of the criteria it is about
                o["routes"] = sorted(
                    {r for cid in o["linked_criteria"] for r in by_id[cid]["routes"]}
                    if all(cid in by_id for cid in o["linked_criteria"])
                    else []
                )
        criteria += observed
        state = {
            "round": 1, "version": 1, "kind": kind, "requirements": req.id,
            "sut": index["sut"], "index": {"routes": len(index["routes"]),
                                           "files": len(index["files"])},
            "criteria": criteria, "findings": findings,
            "rejected": rejected + rej_obs + [{"item": f, "reasons": ["bad evidence"]}
                                              for f in rej_find],
            "unmapped_routes": [], "decisions": [], "history": [], "contradictions": [],
            "asked": [], "pending": [], "followups": [],
        }  # fmt: skip
        state["unmapped_routes"] = [
            f"{r['method']} {r['path']}  ({r['file']}:{r['line']})"
            for r in code_index.reverse_trace(index, criteria)
        ]
        state["candidates"] = questions.candidates(
            found["questions"], by_id | {o["id"]: o for o in observed}
        )
        return state

    # ---- questions ---------------------------------------------------------------

    def _ask(self, ctx, state, path, cands=None) -> StageResult:
        crit = {c["id"]: c for c in state["criteria"]}
        cands = state.pop("candidates", []) if cands is None else cands
        scope = state.get("scope")
        asked, reused, overflow = questions.filter_(cands, crit, set(state["asked"]), scope)
        state["followups"] += overflow
        for q in reused:  # answered before, in any run: apply, don't ask again
            ch = questions.apply_answer(crit.get(q["criterion"]), q, q["answer"]["text"], False)
            if ch:
                state["history"].append({"version": state["version"], "changes": [ch]})
            state["decisions"].append({"key": q["key"], "about": q["about"],
                                       "answer": q["answer"]["text"], "assumed": False,
                                       "reused": True})  # fmt: skip
            ctx.emit("spec_answer_reused", key=q["key"], from_item=q["answer"]["item"])
        written = ai.write_questions(ctx.llm, asked) if asked else []
        state["pending"] = []
        for q in questions.phrase(written, asked):
            state["asked"].append(q["key"])
            payload = {k: q[k] for k in ("key", "criterion", "kind", "about", "changes", "text",
                                         "choices", "recommended")}  # fmt: skip
            payload |= {"round": state["round"], "scope": scope}
            payload["choices"] = [*payload["choices"], {"id": review.OTHER_CHOICE,
                                  "text": "Other: describe what should happen"}]  # fmt: skip
            state["pending"].append(ctx.review.create("question", payload))
        for c in state["criteria"]:
            if c["status"] == "observed" and not c.get("suspected_defect") and not c.get("asked"):
                c["asked"] = True
                payload = {k: c[k] for k in ("title", "given", "when", "then", "status_code")}
                payload |= {"criterion": c["id"], "evidence": c["sources"], "round": state["round"]}
                state["pending"].append(ctx.review.create("confirm_observed", payload))
        path.write_text(json.dumps(state))
        if state["pending"]:
            return StageResult.awaiting_review(state["pending"])
        return self._finish(ctx, state, path)

    def _integrate(self, state, items) -> None:
        crit = {c["id"]: c for c in state["criteria"]}
        changes = []
        for item in items:
            p = item["payload"]
            if item["kind"] == "confirm_observed":
                c = crit[p["criterion"]]
                if item["decision"] == "confirm":
                    c["status"] = "confirmed"
                elif item["decision"] == "out_of_scope":
                    c["status"] = "out_of_scope"
                else:  # reject: the code's behaviour isn't intended -> what should happen?
                    c["status"], c["suspected_defect"] = "observed", True
                    state["followups"].append({
                        "criterion": c["id"], "about": "intended behaviour", "kind": "defect",
                        "why": "A reviewer rejected the code's current behaviour.",
                        "options": [*c.get("alternatives", []), questions.HUMAN,
                                    questions.OUT_OF_SCOPE],
                        "changes": "expected_value",
                    })  # fmt: skip
                continue
            choices = {ch["id"]: ch["text"] for ch in p["choices"]}
            deferred = item["decision"] == "defer"
            picked = None if deferred else item["detail"]["choice"]
            text = (item["detail"]["text"] if picked == review.OTHER_CHOICE
                    else choices[p["recommended"] if deferred else picked])  # fmt: skip
            q = {"kind": p["kind"], "changes": p["changes"]}
            if ch := questions.apply_answer(crit.get(p["criterion"]), q, text, deferred):
                changes.append(ch)
            if (c := crit.get(p["criterion"])) and text == questions.HUMAN:
                c["human_judgement"] = True
            elif c and text == questions.OUT_OF_SCOPE:
                c["status"] = "out_of_scope"
            # Who and which item stay in the review log; the spec holds only the answer, so the
            # same answers give the same spec hash (P5).
            state["decisions"].append({"key": p["key"], "about": p["about"], "answer": text,
                                       "assumed": deferred})  # fmt: skip
        state["contradictions"] += questions.contradictions(changes)
        for con in state["contradictions"]:
            crit[con["criterion"]]["review_needed"] = True
        state["history"].append({"version": state["version"] + 1, "changes": changes})
        state["version"] += 1
        state["pending"] = []

    # ---- exit ----------------------------------------------------------------------

    def _finish(self, ctx, state, path) -> StageResult:
        for c in state["criteria"]:
            if c["status"] in ("draft", "observed"):  # rounds exhausted: assume, flag for review
                c["status"], c["review_needed"] = "assumed", True
                if c.get("suspected_defect"):
                    c["human_judgement"] = True
            c.pop("asked", None)
        if problems := checks.exit_check(state["criteria"], 0):
            return StageResult.failed("exit check: " + "; ".join(problems))
        spec = {k: state[k] for k in ("kind", "requirements", "sut", "version", "index", "criteria",
                                      "findings", "unmapped_routes", "rejected", "decisions",
                                      "history", "contradictions")}  # fmt: skip
        path.unlink(missing_ok=True)
        ctx.emit("spec_frozen", version=spec["version"], criteria=len(spec["criteria"]))
        return StageResult.succeeded({"spec": spec, "spec_md": {"markdown": render(spec)}})


STAGE = register(SpecStage(), cli=cli)
