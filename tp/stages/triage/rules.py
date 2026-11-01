"""HardRules (plan Stage 7, design D2–D4). Pure functions over execute's verdicts.

Order: ENV → DATA → FLAKY_TEST / INTERMITTENT_PRODUCT → TEST_DEFECT → SPEC_GAP → PRODUCT_DEFECT.
Anything the rules leave open is INSUFFICIENT_EVIDENCE (the AI judge is M7)."""

PRODUCT_SIDE = {"PRODUCT_ERROR", "ASSERTION", "CONTRACT"}
TEST_SIDE = {"BINDING", "CONFORMANCE", "PLATFORM"}
CLASSES = ("ENV", "DATA", "FLAKY_TEST", "INTERMITTENT_PRODUCT", "TEST_DEFECT", "SPEC_GAP",
           "PRODUCT_DEFECT", "INSUFFICIENT_EVIDENCE")  # fmt: skip


def _origins(vd: dict, rows: dict) -> set:
    return {rows[a].get("origin") for a in vd["attempts"]
            if a in rows and rows[a].get("outcome") not in (None, "passed")}  # fmt: skip


def classify(vd: dict, ref: dict | None, rows: dict) -> tuple[str, str]:
    """(class, rule) for one non-passing verdict. ref: the same test's verdict on reference."""
    v = vd["verdict"]
    if v in ("SUSPECT_ENV", "NOT_RUN") and (
        v == "SUSPECT_ENV" or vd.get("reason", "").startswith("ENV")
    ):
        return "ENV", f"{v}: the environment was not healthy around this batch"
    if v == "NOT_RUN":
        return "TEST_DEFECT", f"NOT_RUN: {vd.get('reason')}"
    origins = _origins(vd, rows)
    if "ENV" in origins:
        return "ENV", "a failing attempt's origin is ENV"
    if "DATA" in origins:
        return "DATA", "a failing attempt's origin is DATA (setup/factory)"
    if v == "INTERMITTENT":
        if origins and origins <= TEST_SIDE:
            return "FLAKY_TEST", "intermittent, failures raised in the test's own code"
        if origins <= PRODUCT_SIDE and vd.get("escalation", {}).get("escalated"):
            return (
                "INTERMITTENT_PRODUCT",
                "intermittent on the SUT's responses, stable on reference",
            )
        return "INSUFFICIENT_EVIDENCE", "intermittent, not stable on reference"
    if v == "TIMEOUT":
        return "INSUFFICIENT_EVIDENCE", "every attempt timed out"
    if vd["variant"] != "reference" and (ref is None or ref["verdict"] != "PASS"):
        return "SAME_AS_REFERENCE", "fails on reference too: not a finding of this variant"
    if origins & TEST_SIDE:
        return "TEST_DEFECT", f"consistent failure raised in the test ({sorted(origins)})"
    if vd["gate_status"] == "SPEC_GAP":
        return "SPEC_GAP", "the criterion is only assumed (gate SPEC_GAP)"
    if v == "CONSISTENT_FAIL" and origins <= PRODUCT_SIDE:
        return "PRODUCT_DEFECT", "consistent failure on the SUT's responses"
    return "INSUFFICIENT_EVIDENCE", "no rule applies"


def findings(verdicts: list[dict], rows: dict) -> list[dict]:
    ref = {v["node"]: v for v in verdicts if v["variant"] == "reference"}
    out = []
    for vd in verdicts:
        if vd["verdict"] == "PASS":
            continue
        cls, rule = classify(vd, ref.get(vd["node"]), rows)
        if cls == "SAME_AS_REFERENCE":
            continue  # attached to the reference finding below (design D3)
        f = {"id": f"F-{vd['variant']}/{vd['case_id']}", "class": cls, "rule": rule,
             "variant": vd["variant"], "case_id": vd["case_id"], "ac": vd["ac"], "node": vd["node"],
             "verdict": vd["verdict"], "dist": vd["dist"], "evidence": vd["attempts"],
             "seeded": vd["variant"] != "reference"}  # fmt: skip
        if vd["variant"] == "reference":
            f["also_fails_on"] = sorted(
                v["variant"] for v in verdicts if v["node"] == vd["node"]
                and v["variant"] != "reference" and v["verdict"] != "PASS")  # fmt: skip
        if "escalation" in vd:
            f["escalation"] = vd["escalation"]
        out.append(f)
    return out


def defects(fs: list[dict]) -> list[dict]:
    """Product findings grouped by (variant, criterion) (design D4)."""
    groups: dict[tuple, list] = {}
    for f in fs:
        if f["class"] in ("PRODUCT_DEFECT", "INTERMITTENT_PRODUCT"):
            groups.setdefault((f["variant"], f["ac"]), []).append(f)
    out = []
    for (variant, ac), members in sorted(groups.items()):
        out.append({"id": f"D-{variant}/{ac}", "variant": variant, "ac": ac,
                    "class": members[0]["class"], "kind": "real" if variant == "reference" else "seeded",
                    "flag": None if variant == "reference" else variant.removeprefix("bug:"),
                    "findings": [f["id"] for f in members], "cases": [f["case_id"] for f in members]})  # fmt: skip
    return out


def quarantine(fs: list[dict], now: str) -> list[dict]:
    """FLAKY_TEST → quarantine entry (plan †: 14 days or 5 runs). Seeded variants never."""
    from datetime import datetime, timedelta

    expires = (datetime.fromisoformat(now) + timedelta(days=14)).isoformat()
    return [{"case_id": f["case_id"], "owner": "test platform", "reason": f["rule"],
             "evidence": f["evidence"][0], "expires": expires, "max_runs": 5}
            for f in fs if f["class"] == "FLAKY_TEST" and not f["seeded"]]  # fmt: skip
