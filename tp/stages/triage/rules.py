"""HardRules (plan Stage 7, design D2–D4). Pure functions over execute's verdicts.

Order: ENV → DATA → FLAKY_TEST / INTERMITTENT_PRODUCT → TEST_DEFECT → SPEC_MISMATCH → SPEC_GAP
→ PRODUCT_DEFECT.
Anything the rules leave open is INSUFFICIENT_EVIDENCE (the AI judge is M7)."""

import re
import uuid

PRODUCT_SIDE = {"PRODUCT_ERROR", "ASSERTION", "CONTRACT", "UI_ERROR"}  # UI_ERROR: W0 C7
TEST_SIDE = {"BINDING", "CONFORMANCE", "PLATFORM"}
CLASSES = ("ENV", "DATA", "FLAKY_TEST", "INTERMITTENT_PRODUCT", "TEST_DEFECT", "SPEC_MISMATCH",
           "SPEC_GAP", "PRODUCT_DEFECT", "INSUFFICIENT_EVIDENCE")  # fmt: skip
INFRA = {429, 502, 503, 504}  # rate limited or a gateway in front: the environment (checks/001 D8)
MALFORMED = ("the test sent a path value its spec's parameter schema refuses ({}), and the "
             "server refused it")  # fmt: skip


def _origin(row: dict) -> str | None:
    """Under an injected dependency fault (W0 C7), a timeout talking to the SUT is the SUT failing
    to absorb the fault we injected on purpose: the product's side, not the environment's."""
    return "PRODUCT_ERROR" if row.get("origin") == "ENV" and row.get("fault") else row.get("origin")


def _origins(vd: dict, rows: dict) -> set:
    return {_origin(rows[a]) for a in vd["attempts"]
            if a in rows and rows[a].get("outcome") not in (None, "passed")}  # fmt: skip


def _faulted(vd: dict, rows: dict) -> bool:
    return any(rows[a].get("fault") for a in vd["attempts"] if a in rows)


def under_test(row: dict) -> dict | None:
    """The exchange a failing record asserted on (its last test request if none was marked).
    A UI action (`kind: ui`) is a step, not a request: a flow that failed before it marked one
    falls back to its last HTTP exchange (integration/002; C7 still reads the origin)."""
    steps = [e for e in row.get("exchanges", []) if not e.get("setup") and e.get("kind") != "ui"]
    return next(
        (e for e in steps if e["step"] == row.get("under_test")), steps[-1] if steps else None
    )


def _statuses(vd: dict, rows: dict) -> set:
    return {(under_test(rows[a]) or {}).get("status") for a in vd["attempts"]
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
    # a gateway status under an injected fault is the SUT's answer to it, not the environment (C7)
    if not _faulted(vd, rows) and (statuses := _statuses(vd, rows)) and statuses <= INFRA:
        return "ENV", f"the target answered {sorted(statuses)}: rate limited or a gateway error"
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
    if "SPEC" in origins:
        return (
            "SPEC_MISMATCH",
            "valid HTTP that disagrees with the served spec: server or spec is wrong",
        )
    if vd["gate_status"] == "SPEC_GAP":
        return "SPEC_GAP", "the criterion is only assumed (gate SPEC_GAP)"
    if v == "CONSISTENT_FAIL" and origins <= PRODUCT_SIDE:
        return "PRODUCT_DEFECT", "consistent failure on the SUT's responses"
    return "INSUFFICIENT_EVIDENCE", "no rule applies"


def _bad(value: str, schema: dict) -> bool:
    """The value can't satisfy the schema's type, format, pattern or enum."""
    t, fmt = schema.get("type"), schema.get("format")
    if t == "integer":
        return not re.fullmatch(r"-?\d+", value)
    if t == "number":
        return not re.fullmatch(r"-?\d+(\.\d+)?", value)
    if fmt == "uuid":
        try:
            uuid.UUID(value)
        except ValueError:
            return True
    if (p := schema.get("pattern")) and not re.search(p, value):
        return True
    return "enum" in schema and value not in [str(e) for e in schema["enum"]]


def malformed_path(ex: dict | None, openapi: dict | None) -> str | None:
    """`name=value is not a valid <type>` when a path value of the request can't satisfy its
    OpenAPI parameter schema, else None. Paths are matched as the spec writes them (full paths).
    shortcut: a `servers` base path is not prepended; add it when a target's spec uses one."""
    if not ex or not openapi:
        return None
    got = ex["path"].rstrip("/").split("/")
    for route, ops in (openapi.get("paths") or {}).items():
        op = ops.get(ex["method"].lower())
        want = route.rstrip("/").split("/")
        if (
            not op
            or len(want) != len(got)
            or any(
                w != g and not (w.startswith("{") and w.endswith("}")) for w, g in zip(want, got)
            )
        ):
            continue
        params = [_deref(openapi, p) for p in ops.get("parameters", []) + op.get("parameters", [])]
        by_name = {p.get("name"): p for p in params if p.get("in") == "path"}
        for w, g in zip(want, got):
            if w.startswith("{") and (p := by_name.get(w[1:-1])):
                schema = _deref(openapi, p.get("schema") or {})
                schema = next((_deref(openapi, s) for s in schema.get("anyOf", [])
                               if s.get("type") != "null"), schema)  # fmt: skip
                if _bad(g, schema):
                    kind = schema.get("format") or schema.get("type") or "value"
                    return f"{w[1:-1]}={g} is not a valid {kind}"
        return None
    return None


def _deref(spec: dict, node: dict) -> dict:
    for _ in range(10):
        ref = node.get("$ref", "") if isinstance(node, dict) else ""
        if not ref.startswith("#/"):
            return node
        node = spec
        for part in ref[2:].split("/"):
            node = node.get(part, {})
    return node


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
