"""Provenance labels. A child carries every label of its parents."""

LABELS = ("real", "simulated", "pre-generated", "recorded", "unsigned")


def check(labels: list[str]) -> list[str]:
    bad = set(labels) - set(LABELS)
    if bad:
        raise ValueError(f"unknown labels: {sorted(bad)}")
    return sorted(set(labels))


def inherit(parent_labels: list[list[str]], own: list[str]) -> list[str]:
    return check([lbl for labels in parent_labels for lbl in labels] + own)


def for_mode(mode: str, stub: bool) -> list[str]:
    if stub:
        return ["simulated"] + (["recorded"] if mode == "replay" else [])
    return ["recorded" if mode == "replay" else "real"]
