"""Bind every case unit: scaffold → write → static checks → DryRun → repair (plan G4–G6).

guards (design D9):
  v1  repair any failure (ASSERTION included); conformance off; CASE_DATA outside the hash.
  v2  repair only binding faults (static, BINDING, CONFORMANCE); conformance on; CASE_DATA in
      the hash; anything else that fails is the product's business (the gate routes it).
"""

import difflib
import json
import shutil
from pathlib import Path

from tp.core.hashing import sha256
from tp.stages.generate import ai, checks
from tp.stages.generate import scaffold as S

MAX_REPAIRS = 3  # † attempts after the first write (plan G-RepairLoop)
REPAIRABLE = {"v1": {"BINDING", "CONFORMANCE", "ASSERTION", "PRODUCT_ERROR", "CONTRACT", "DATA"},
              "v2": {"BINDING", "CONFORMANCE"}}  # fmt: skip
NOT_THE_TESTS_FAULT = {"ENV", "PLATFORM", "TIMEOUT"}  # never repaired: the binding can't fix them


def classify(static: list[str], failures: list[dict]) -> str:
    """Ledger classification of a failed attempt (plan Ledger: locator / fixture / generation).
    shortcut: keyword heuristics over the error text; upgrade if the ledger is used for metrics."""
    if static:
        return "generation"
    origins = {f["origin"] for f in failures}
    if origins - REPAIRABLE["v2"]:
        return "product" if origins & {"ASSERTION", "PRODUCT_ERROR", "CONTRACT"} else "environment"
    text = " ".join(f.get("error", "") for f in failures)
    if "make." in text or "Factor" in text or "owner" in text:
        return "fixture"
    if "CONFORMANCE" in origins:
        return "generation"
    if any(s in text for s in ("KeyError", "404", "405", "path", "JSONDecodeError")):
        return "locator"
    return "generation"


def _failures(records: list[dict]) -> list[dict]:
    return [{"case_id": r["case_id"], "origin": r["origin"], "error": r["failure"]["error"],
             "line": r["failure"]["line"],
             "sent": [{k: e[k] for k in ("method", "path", "query", "json", "status")}
                      | {"body": str(e["body"])[:300]} for e in r["exchanges"] if not e["setup"]]}
            for r in records if r["outcome"] == "failed"]  # fmt: skip


def _failure_text(static: list[str], failures: list[dict]) -> str:
    if static:
        return "The static checker rejected the file:\n" + "\n".join(f"- {s}" for s in static)
    return "\n\n".join(f"Case {f['case_id']}: origin {f['origin']} at line {f['line']}\n"
                       f"{f['error'][:800]}\nRequests the binding sent:\n"
                       f"{json.dumps(f['sent'], indent=1)[:1500]}"
                       for f in failures[:3])  # fmt: skip


def bind_all(ctx, units: list[dict], criteria: dict, openapi: dict, workdir: Path,
             guards: str = "v2", variant: str = "reference",
             max_repairs: int = MAX_REPAIRS) -> tuple[dict, list[dict]]:  # fmt: skip
    """-> ({unit id: test entry}, ledger attempts)."""
    tests, ledger, live = {}, [], {}
    for u in units:
        routes = criteria[u["ac"]]["routes"]
        try:
            sc = S.scaffold(u, routes, openapi, guards=guards)
        except S.ScaffoldError as e:
            tests[u["id"]] = {"unit": u["id"], "status": "UNBOUND", "reason": str(e)}
            continue
        setup = [ln.split("=")[0].strip() for ln in S.setup_lines(u.get("preconditions", []))]
        text = ai.task(u, sc, routes, openapi, setup)
        out = ai.write(ctx.llm, text)
        entry = {"unit": u["id"], "ac": u["ac"], "file": sc["file"], "lock_hash": sc["lock_hash"],
                 "case_ids": [p["id"] for p in sc["params"]], "routes": routes,
                 "case_data": {p["id"]: p["case_data"] for p in sc["params"]},
                 "attempts": 1}  # fmt: skip
        if out["cannot_bind"] or not out["binding"].strip():
            reason = out["cannot_bind"] or "the binding writer returned nothing"
            tests[u["id"]] = entry | {"status": "UNBOUND", "reason": reason}
            ledger.append({"unit": u["id"], "attempt": 1, "kind": "write", "outcome": "declined",
                           "reason": reason, "classification": None})  # fmt: skip
            continue
        live[u["id"]] = {"entry": entry, "sc": sc, "task": text, "binding": out["binding"],
                         "history": [], "kind": "write"}  # fmt: skip
    ctx.emit("bindings_written", units=len(units), live=len(live))

    for rnd in range(max_repairs + 1):
        if not live:
            break
        rdir = workdir / f"round-{rnd}"
        shutil.rmtree(rdir, ignore_errors=True)
        (rdir / "tests").mkdir(parents=True)
        static = {}
        for uid, st in live.items():
            src = S.with_binding(st["sc"]["source"], st["binding"])
            st["source"] = src
            meta = {"unit": uid, "case_ids": st["entry"]["case_ids"],
                    "lock_hash": st["sc"]["lock_hash"], "guards": guards}  # fmt: skip
            static[uid] = checks.run(src, meta)
            if not static[uid]:
                (rdir / "tests" / st["sc"]["file"]).write_text(src)
        records: dict[str, list[dict]] = {uid: [] for uid in live}
        if any(not p for p in static.values()):
            env = {"TP_CONFORMANCE": "0" if guards == "v1" else "1", "TP_RUN": workdir.name}
            dry = ctx.env.pytest(rdir / "tests", rdir / "out", variant, env=env)
            by_file = {st["sc"]["file"]: uid for uid, st in live.items()}
            for r in dry["results"]:
                uid = by_file.get(r["nodeid"].split("::")[0].split("/")[-1])
                if uid:
                    records[uid].append(r)
        ctx.emit("dry_run", round=rnd, units=len(live))
        for uid in list(live):
            st = live[uid]
            fails = _failures(records[uid])
            origins = {f["origin"] for f in fails}
            missing = not static[uid] and len(records[uid]) != len(st["entry"]["case_ids"])
            prev = st["history"][-1]["binding"] if st["history"] else ""
            attempt = {"unit": uid, "attempt": st["entry"]["attempts"], "kind": st["kind"],
                       "binding_sha": sha256(st["binding"])[:12],
                       "diff": "\n".join(difflib.unified_diff(prev.splitlines(),
                                         st["binding"].splitlines(), lineterm="", n=1)),
                       "static": static[uid],
                       "origins": {r["case_id"]: r["origin"] for r in records[uid]},
                       "classification": None}  # fmt: skip
            repair = bool(static[uid]) or bool(origins & REPAIRABLE[guards]) or missing
            if not repair:
                status = "ERROR" if origins & (NOT_THE_TESTS_FAULT | {"DATA"}) else "BOUND"
                attempt |= {"outcome": "bound" if status == "BOUND" else "error"}
                ledger.append(attempt)
                tests[uid] = st["entry"] | {"status": status, "source": st["source"],
                                            "dry_run": attempt["origins"],
                                            "failures": fails}  # fmt: skip
                del live[uid]
                continue
            attempt["classification"] = classify(static[uid], fails)
            failure = _failure_text(static[uid], fails) if not missing else (
                "pytest produced no result for some cases (the file did not collect).")  # fmt: skip
            st["history"].append({"attempt": attempt["attempt"], "binding": st["binding"],
                                  "outcome": attempt["classification"]})  # fmt: skip
            if st["entry"]["attempts"] > max_repairs:
                attempt["outcome"] = "unbound"
                ledger.append(attempt)
                tests[uid] = st["entry"] | {"status": "UNBOUND", "source": st["source"],
                                            "reason": f"still failing after {max_repairs} repairs",
                                            "failures": fails, "static": static[uid]}  # fmt: skip
                del live[uid]
                continue
            new = ai.repair(ctx.llm, st["task"], guards, failure, st["history"])
            seen = {sha256(h["binding"]) for h in st["history"]}
            attempt["outcome"] = "rejected" if static[uid] else "repair"
            ledger.append(attempt)
            if new["cannot_bind"] or sha256(new["binding"]) in seen:
                why = new["cannot_bind"] or "loop: the repair repeated an earlier binding"
                ledger.append({"unit": uid, "attempt": st["entry"]["attempts"] + 1,
                               "kind": "repair", "outcome": "unbound", "reason": why,
                               "classification": None})  # fmt: skip
                tests[uid] = st["entry"] | {"status": "UNBOUND", "source": st["source"],
                                            "reason": why, "failures": fails}  # fmt: skip
                del live[uid]
                continue
            st["binding"], st["kind"] = new["binding"], "repair"
            st["entry"]["attempts"] += 1
    return tests, ledger
