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


# ---- per test record and per run (iteration 3, W0 C2; DR-3, DR-7) --------------------------------
# The artifact labels above say how an output was made; these say what one test's verdict rests
# on, in the same words, so the run header, the report and the API can't label one result twice.

STANDIN_URL = "://tp-standin-"  # C8: stand-ins are services named tp-standin-<name>


def basis(record: dict) -> str:
    """`pre-generated`: a community suite (`kind` "suite"). `simulated`: the verdict rests on a
    stand-in, through an injected fault or an exchange with one (P6 records a journal read as an
    exchange whose `path` is the stand-in's absolute URL). Otherwise `real`. A record written
    before platform_rt 0.9.0 has neither `kind` nor `fault`, and is real."""
    if record.get("kind") == "suite":
        return "pre-generated"
    if record.get("fault") or any(
        STANDIN_URL in str(e.get("path", "")) for e in record.get("exchanges") or []
    ):
        return "simulated"
    return "real"


def isolation(profile) -> int:
    """2: a live system the platform doesn't run (external reach). 1: the SUT runs in the sandbox,
    with local stand-ins for dependencies that can't be containerised. 0: fully isolated."""
    if profile.reach.strategy == "external":
        return 2
    return 1 if profile.reach.value.get("standins") else 0
