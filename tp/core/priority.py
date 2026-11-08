"""Priority scores from config/priority.yaml (iteration 2, seams/001 A2).

A score is the sum of the weights of the terms that hold, returned with its parts so the UI can
show why an item ranks where it does. Weights and label thresholds are configuration, not code.
"""

from functools import cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "priority.yaml"


@cache
def config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def score(terms: dict[str, bool], weights: dict[str, int]) -> dict:
    """{score, parts}: `parts` lists each term that held with its weight. Unknown terms fail
    loudly so a typo can't silently score 0."""
    unknown = set(terms) - set(weights)
    if unknown:
        raise KeyError(f"no weight for {sorted(unknown)} in {CONFIG.name}")
    parts = {t: weights[t] for t, held in terms.items() if held and weights[t]}
    return {"score": sum(parts.values()), "parts": parts}


def label(value: int, labels: dict[str, int], default: str) -> str:
    for name, threshold in sorted(labels.items(), key=lambda kv: -kv[1]):
        if value >= threshold:
            return name
    return default


def case(terms: dict[str, bool]) -> dict:
    """A case unit's {score, parts, priority} under the configured case weights."""
    c = config()["case"]
    s = score({"base": True, **terms}, c["weights"])
    return s | {"priority": label(s["score"], c["labels"], c["default_label"])}


def severity(defect: dict) -> str:
    """The fixed severity rule (autonomy/001 F6), from the failed status assertion."""
    status = [
        a for a in defect.get("failed_assertions", []) if str(a.get("id", "")).endswith("/status")
    ]
    actual = (defect.get("observed") or {}).get("status")
    actual = status[0].get("actual", actual) if status else actual
    expected = str(status[0].get("expected", "")) if status else ""
    if isinstance(actual, int) and actual >= 500:
        return "server_error"
    if isinstance(actual, int) and 200 <= actual < 300 and expected[:1] == "4":
        return "permission_bypass" if expected in ("401", "403") else "validation_gap"
    return "wrong_value"


def pr(defect: dict, strong_evidence: bool, rules: dict[str, bool]) -> dict:
    """A defect's {score, parts, rules, pr}: pr only if score >= threshold and every hard rule
    holds. `rules` maps each hard rule's name to whether it held, so the UI can show which failed."""
    c = config()["pr"]
    sev = severity(defect)
    ev = "plus_planted_bug_or_property" if strong_evidence else "stable"
    s = c["severity"][sev] * c["evidence"][ev]
    return {"score": s, "parts": {sev: c["severity"][sev], ev: c["evidence"][ev]},
            "threshold": c["threshold"], "rules": rules,
            "pr": s >= c["threshold"] and all(rules.values())}  # fmt: skip
