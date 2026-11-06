"""Flow mapping and the SignalScanner (plan Stage 2, C1): deterministic minimum template sets."""

import re

from tp.core import profile

_THRESHOLD = re.compile(
    r"\b\d+\s*(characters?|chars?|items?|days?|minutes?|bytes?)\b|\b(maximum|minimum|max|min|"
    r"limit|at most|at least|up to|longer than|shorter than|page size|skip)\b"
)
_ACTOR = re.compile(r"\b(other users?|another user|someone else|owner|administrator|superuser|"
                    r"anonymous|not their own|belong(s|ing)? to)\b")  # fmt: skip
_FAILURE = re.compile(
    r"\b(4\d\d|5\d\d|refused?|reject(ed)?|cannot|denied|forbidden|error|fails?)\b"
)


def flow_of(criterion: dict) -> str | None:
    """The flow of a criterion's first mapped route ('METHOD <base>/...'). The target's profile
    may name its flows (`spec.value.flows`: [prefix, flow], most specific first, design D2);
    otherwise a route's flow is its first path segment under the API base."""
    spec = profile.load().spec.value
    flows, base = spec.get("flows") or [], spec.get("base", "")
    for route in criterion.get("routes", []):
        path = route.split(" ", 1)[1].removeprefix(base)
        for prefix, flow in flows:
            if path.startswith(prefix):
                return flow
        if not flows and (segment := path.strip("/").split("/")[0]):
            return f"FLOW-{segment}"
    return None


def _text(c: dict) -> str:
    keys = ("title", "given", "when", "then", "resolution")
    return " ".join(str(c.get(k) or "") for k in keys).lower()


def minimum_templates(c: dict) -> list[str]:
    """The templates a criterion must use (or skip with a reason)."""
    text, out = _text(c), []
    if _THRESHOLD.search(text):
        out.append("boundary")
    if _ACTOR.search(text):
        out.append("decision_table")
    if (c.get("status_code") or 0) >= 400 or _FAILURE.search(text):
        out.append("error_exception")
    if c.get("suspected_defect"):
        out.append("error_guessing")
    return out
