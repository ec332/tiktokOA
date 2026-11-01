"""Canonical JSON and sha256. Same object -> same bytes -> same hash, in any process."""

import hashlib
import json
import math
from typing import Any


def _normalise(obj: Any) -> Any:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("NaN/inf cannot be hashed")
        return 0.0 if obj == 0 else obj  # -0.0 -> 0.0; json uses shortest round-trip repr
    if isinstance(obj, str):
        return obj.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(obj, dict):
        if not all(isinstance(k, str) for k in obj):
            raise TypeError("object keys must be strings")
        return {_normalise(k): _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    raise TypeError(f"not canonical-JSON-able: {type(obj).__name__}")


def canonical(obj: Any) -> bytes:
    return json.dumps(
        _normalise(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def sha256(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()
