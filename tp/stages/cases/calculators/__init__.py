"""Expected-value calculators and independent property checks (design D1).

Hand-written and unit-tested: the LLM picks one and its parameters, never the value.
"""

import inspect

CALCULATORS: dict = {}
PROPERTIES: dict = {}


def calculator(fn):
    CALCULATORS[fn.__name__] = fn
    return fn


def prop(fn):
    PROPERTIES[fn.__name__] = fn
    return fn


@calculator
def boundary_points(limit: int, step: int = 1) -> list[int]:
    """The three points around a limit: just below, at, just above."""
    return [limit - step, limit, limit + step]


@calculator
def boundary_expect(
    value: int, limit: int, inclusive: bool, ok: str, fail: str, direction: str = "max"
) -> str:
    """The expected outcome for one boundary point: `ok` on the allowed side, else `fail`.
    direction "max": values up to the limit are allowed; "min": values from the limit up."""
    if direction == "min":
        allowed = value >= limit if inclusive else value > limit
    else:
        allowed = value <= limit if inclusive else value < limit
    return ok if allowed else fail


@calculator
def page_window(total: int, skip: int, limit: int) -> dict:
    """A list page: how many items come back and the total reported."""
    return {"count": total, "data_len": max(0, min(limit, total - skip))}


@prop
def page_len_ok(total: int, skip: int, limit: int, data_len: int) -> bool:
    """Independent of page_window: never more than the limit or what's left, and a short page
    only when the list ran out."""
    left = max(0, total - skip)
    return data_len <= limit and data_len <= left and (data_len == limit or data_len == left)


@calculator
def newest_first(created: int) -> list[int]:
    """List order for `created` items made one after another (1 = first made): newest first."""
    return list(range(created, 0, -1))


@prop
def boundary_monotonic(points: list[int], outcomes: list[str], ok: str, direction: str) -> bool:
    """Moving away from the allowed side, once a point is refused every further point is too:
    upwards for a maximum, downwards for a minimum."""
    seen_fail = False
    pairs = sorted(zip(points, outcomes, strict=True), reverse=direction == "min")
    for _, outcome in pairs:
        seen_fail = seen_fail or outcome != ok
        if seen_fail and outcome == ok:
            return False
    return True


def describe() -> list[dict]:
    """The catalogue the drafter sees: name, signature, purpose."""
    return [
        {"name": n, "signature": str(inspect.signature(f)), "doc": inspect.getdoc(f)}
        for n, f in CALCULATORS.items()
    ]


def run(name: str, params: dict):
    """Call a calculator with params coerced to its annotated types (the drafter sends strings)."""
    fn = CALCULATORS[name]
    sig = inspect.signature(fn)
    args = {}
    for k, p in sig.parameters.items():
        if k not in params:
            if p.default is inspect.Parameter.empty:
                raise ValueError(f"{name}: missing parameter {k}")
            continue
        v = params[k]
        if p.annotation is int:
            v = int(v)
        elif p.annotation is bool:
            v = v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1")
        args[k] = v
    return fn(**args)
