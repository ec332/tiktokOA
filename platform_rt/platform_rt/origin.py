"""OriginClassifier (plan G3): why did a test fail? From the phase, the exception, where it was
raised and the recorded exchanges (+ the OpenAPI schema for CONTRACT). Pure function."""

from pathlib import Path

from platform_rt.api import ConformanceError, route_matches
from platform_rt.expect import ExpectationFailed, MissingObservation

ORIGINS = ("ENV", "DATA", "BINDING", "CONTRACT", "PRODUCT_ERROR", "ASSERTION", "CONFORMANCE",
           "PLATFORM", "TIMEOUT")  # fmt: skip
HERE = str(Path(__file__).parent)
# By name: these come from libraries platform_rt does not import (sqlalchemy, the env factories).
ENV_ERRORS = {"ConnectError", "ConnectTimeout", "ReadTimeout", "RemoteProtocolError",
              "OperationalError", "InterfaceError", "EnvDown"}  # fmt: skip
DATA_ERRORS = {"FactoryError"}


class TestTimeout(Exception):
    __test__ = False  # not a test class


def _names(exc: BaseException) -> set[str]:
    return {c.__name__ for c in type(exc).__mro__}


def resolve(schema: dict, node: dict) -> dict:
    for _ in range(10):  # $ref chains in FastAPI schemas are short
        ref = node.get("$ref")
        if not ref:
            return node
        node = schema
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
    return node


def contract_problem(exchange: dict, schema: dict | None) -> str | None:
    """A 2xx body missing a property the documented response schema requires.
    shortcut: shallow required-keys check only; upgrade to full JSON Schema validation when a
    criterion depends on nested response shape."""
    if not schema or exchange["status"] // 100 != 2:
        return None
    for path, ops in schema.get("paths", {}).items():
        op = ops.get(exchange["method"].lower())
        if not op or not route_matches(f"{exchange['method']} {path}", exchange["method"],
                                       exchange["path"]):  # fmt: skip
            continue
        resp = op.get("responses", {}).get(str(exchange["status"])) or {}
        body_schema = resp.get("content", {}).get("application/json", {}).get("schema")
        if not body_schema:
            return None
        body_schema = resolve(schema, body_schema)
        required = body_schema.get("required", [])
        if required and not isinstance(exchange["body"], dict):
            return f"body is not an object (schema requires {required})"
        if missing := [k for k in required if k not in exchange["body"]]:
            return f"body lacks required {missing}"
        return None
    return None


def incomplete_request(exchange: dict, case_data: dict | None) -> list[str]:
    """Fields a 422 says are missing that the case never asked to omit: the binding left out a
    required field, so the 422 is the binding's, not the product's (G4 run).
    shortcut: reads FastAPI/pydantic's validation shape; other frameworks need their own."""
    if exchange["status"] != 422 or not isinstance(exchange["body"], dict):
        return []
    detail = exchange["body"].get("detail")
    asked = set(case_data or {})
    return [str(d["loc"][-1]) for d in detail if isinstance(d, dict) and d.get("type") == "missing"
            and d.get("loc") and str(d["loc"][-1]) not in asked] if isinstance(detail, list) else []  # fmt: skip


def classify(phase: str, exc: BaseException, raised_in: str, exchanges: list[dict],
             under_test: dict | None, schema: dict | None = None,
             case_data: dict | None = None) -> str:  # fmt: skip
    """phase: collect|setup|call|teardown. raised_in: file of the innermost traceback frame."""
    names = _names(exc)
    if isinstance(exc, TestTimeout):
        return "TIMEOUT"
    if names & ENV_ERRORS:
        return "ENV"
    if names & DATA_ERRORS or phase in ("setup", "teardown"):
        return "DATA"  # a factory, a fixture or the leak guard: the test data, not the test
    if phase == "collect":
        return "BINDING"
    if isinstance(exc, ConformanceError):
        return "CONFORMANCE"
    expected = isinstance(exc, (ExpectationFailed, MissingObservation))
    if raised_in.startswith(HERE) and not expected:
        return "PLATFORM"
    steps = [e for e in exchanges if not e["setup"]]
    product = under_test or (steps[-1] if steps and not expected else None)
    if product and product["status"] >= 500:
        return "PRODUCT_ERROR"
    if product and contract_problem(product, schema):
        return "CONTRACT"
    if isinstance(exc, ExpectationFailed):
        return (
            "BINDING" if under_test and incomplete_request(under_test, case_data) else "ASSERTION"
        )
    return "BINDING"
