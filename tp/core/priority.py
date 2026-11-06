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
