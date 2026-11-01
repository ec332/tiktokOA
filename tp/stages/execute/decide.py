"""Deterministic parts of execute: lanes, verdicts, escalation (design D1, D5, D11). Pure."""

ESCALATE_K = 10  # observation lane runs (plan †)
TEST_SIDE = {"BINDING", "CONFORMANCE", "PLATFORM"}  # the test's own code failed, not the SUT


def lane(gate_status: str) -> str:
    """Every gate row runs (D1); the lane only labels why."""
    return {"CANDIDATE_DEFECT": "main:candidate", "SPEC_GAP": "main:spec_gap",
            "WEAK": "main:weak"}.get(gate_status, "main")  # fmt: skip


def signature(row: dict) -> tuple:
    """What a failure looked like: origin + the failed assertion ids."""
    return (
        row.get("origin"),
        tuple(sorted(a["id"] for a in row.get("assertions", []) if not a["ok"])),
    )


def verdict(attempts: list[dict]) -> dict:
    """attempts: the main-lane rows of one (test, variant). Plan D11 / Stage 6 `Verdicts`."""
    n = len(attempts)
    if any(a["status"] == "SUSPECT_ENV" for a in attempts):
        return {"verdict": "SUSPECT_ENV", "dist": f"?/{n}"}
    if any(a["status"] == "NOT_RUN" for a in attempts):
        reasons = sorted({a.get("reason", "ENV") for a in attempts if a["status"] == "NOT_RUN"})
        return {"verdict": "NOT_RUN", "dist": f"?/{n}", "reason": ",".join(reasons)}
    failed = [a for a in attempts if a["outcome"] != "passed"]
    dist = f"{len(failed)}/{n}"
    if not failed:
        return {"verdict": "PASS", "dist": dist}
    sigs = {signature(a) for a in failed}
    if len(failed) == n and len(sigs) == 1:
        origin = failed[0].get("origin")
        return {"verdict": "TIMEOUT" if origin == "TIMEOUT" else "CONSISTENT_FAIL", "dist": dist,
                "origin": origin, "assertions": list(signature(failed[0])[1])}  # fmt: skip
    return {"verdict": "INTERMITTENT", "dist": dist, "signatures": sorted(map(str, sigs))}


def escalation(on_variant: list[dict], on_reference: list[dict]) -> dict:
    """Observation lane result: escalated when the test is stable on reference but not here."""
    f_var = sum(r["outcome"] != "passed" for r in on_variant)
    f_ref = sum(r["outcome"] != "passed" for r in on_reference)
    return {"variant": f"{f_var}/{len(on_variant)}", "reference": f"{f_ref}/{len(on_reference)}",
            "escalated": f_var > 0 and f_ref == 0 and bool(on_reference)}  # fmt: skip
