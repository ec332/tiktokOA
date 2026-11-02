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


ROUNDING = {"half_up": "ROUND_HALF_UP", "half_even": "ROUND_HALF_EVEN", "down": "ROUND_DOWN"}


@calculator
def percent_off(amount: str, percent: str, rounding: str = "half_up", places: int = 2) -> str:
    """A price after a percentage discount: amount × (100 − percent) / 100, rounded to `places`
    decimals with `rounding` (half_up, half_even or down). Decimal arithmetic, returned as a
    string with exactly `places` decimals, e.g. ("10.05", "15") -> "8.54"."""
    import decimal

    if rounding not in ROUNDING:
        raise ValueError(f"rounding must be one of {sorted(ROUNDING)}")
    exact = decimal.Decimal(amount) * (100 - decimal.Decimal(percent)) / 100
    q = decimal.Decimal(1).scaleb(-places)
    return str(exact.quantize(q, rounding=getattr(decimal, ROUNDING[rounding])))


@prop
def percent_off_bounded(amount: str, percent: str, result: str) -> bool:
    """Independent of percent_off: for 0-100 percent the result lies between 0 and the amount,
    within one unit of the last place of the exact value."""
    from fractions import Fraction

    a, p, r = Fraction(amount), Fraction(percent), Fraction(result)
    exact = a * (100 - p) / 100
    return 0 <= r <= a and abs(r - exact) <= Fraction(1, 100)


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
