"""Deterministic parts of retest: same tests, env diff, fail→pass, pass→pass, verdict. Pure.
Design: docs/status/retest/001."""

import hashlib

K = 5  # fail→pass attempts (plan †)
# shortcut: K is fixed; an intermittent defect with rate p needs K with (1-p)^K < 0.05. No such
# defect exists yet (the race fails 3/3); compute K from the before-run's rate when one does.
CODE_OR_FLAGS = {"flags", "images.backend", "services.backend"}  # may differ (D4)
UNSURE = ("SUSPECT_ENV", "NOT_RUN")


def test_hashes(bound: dict) -> dict[str, tuple[str, str]]:
    """file -> (sha256 of the source, lock_hash of its assertions) for every bound test."""
    return {
        t["file"]: (hashlib.sha256(t["source"].encode()).hexdigest(), t.get("lock_hash"))
        for t in bound["tests"]
        if t.get("status") == "BOUND"
    }


def same_tests(before: dict, after: dict) -> list[str]:
    """Test files whose source or assertion hash differ from the failing run's (D2)."""
    a, b = test_hashes(before), test_hashes(after)
    return sorted(f for f in a.keys() | b.keys() if a.get(f) != b.get(f))


def _flat(fp: dict) -> dict:
    """{"images": {"backend": x}} -> {"images.backend": x}; `sut` is compared on its own."""
    out = {}
    for k, v in fp.items():
        if isinstance(v, dict) and k != "sut":
            out |= {f"{k}.{kk}": vv for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def env_diff(before: dict | None, after: dict | None) -> list[str]:
    """Fingerprint fields that differ beyond code and flags (D4). Empty = comparable."""
    if not before or not after:
        return ["fingerprint missing: re-run execute (>= 1.1.0) for the failing run"]
    a, b = _flat(before), _flat(after)
    keys = sorted((a.keys() | b.keys()) - CODE_OR_FLAGS - {"sut"})
    out = [k for k in keys if a.get(k) != b.get(k)]
    sa, sb = dict(a.get("sut") or {}), dict(b.get("sut") or {})
    pa, pb = sa.pop("patches", {}), sb.pop("patches", {})
    if sa != sb:
        out.append("sut (name/ref)")
    if any(pb.get(name) != h for name, h in pa.items()):  # patches may only be added
        out.append("sut.patches (a patch changed or was removed)")
    return out


def reproduced(defect_nodes: list[str], before: dict[str, dict]) -> bool:
    """The defect's tests exist in the failing run and really failed there (else nothing to prove)."""
    return bool(defect_nodes) and all(
        before[n]["verdict"] not in ("PASS", *UNSURE) for n in defect_nodes
    )


def judge(
    defect_nodes: list[str],
    before: dict[str, dict],
    after: dict[str, dict],
    changed: list[str],
    env: list[str],
) -> dict:
    """before/after: node -> execute verdict on the before/after variant. Plan Stage 9 Verdict."""

    def v(side: dict, node: str, key: str = "verdict"):
        return (side.get(node) or {}).get(key)

    f2p = [
        {"node": n, "case_id": v(before, n, "case_id"),
         "before": v(before, n), "before_dist": v(before, n, "dist"),
         "after": v(after, n), "after_dist": v(after, n, "dist")}
        for n in defect_nodes
    ]  # fmt: skip
    passed_before = [n for n in before if v(before, n) == "PASS"]
    seen = reproduced(defect_nodes, before)
    ran = not changed and seen  # otherwise the retest stops before anything runs (D2)
    unsure = sorted(
        {n for n in [*defect_nodes, *passed_before] if ran and v(after, n) in (*UNSURE, None)}
    )
    regressions = [n for n in passed_before if ran and n not in unsure and v(after, n) != "PASS"]
    pre_existing = [
        {
            "node": n,
            "case_id": v(before, n, "case_id"),
            "before": v(before, n),
            "after": v(after, n),
        }
        for n in sorted(before)
        if v(before, n) != "PASS" and n not in defect_nodes
    ]
    if changed:
        verdict = "RETEST_INVALID"
    elif not seen or env or unsure:
        verdict = "INCONCLUSIVE"
    elif any(r["after"] != "PASS" for r in f2p):
        verdict = "NOT_FIXED"
    elif regressions:
        verdict = "FIXED_WITH_REGRESSION"
    else:
        verdict = "FIXED_CONFIRMED"
    return {
        "verdict": verdict,
        "closed": verdict == "FIXED_CONFIRMED",  # a defect closes only on FIXED_CONFIRMED
        "reproduced": seen,
        "fail_to_pass": f2p,
        "pass_to_pass": {"checked": len(passed_before), "regressions": regressions},
        "unsure": unsure,
        "pre_existing": pre_existing,
    }


def fix_kind(before: str, after: str) -> str:
    """D10: what made the difference between the two variants."""
    if after.startswith("fixed:"):
        return "own patch on upstream"
    if before.startswith("bug:") and after == "reference":
        return "flag off (seeded)"
    return "none"
