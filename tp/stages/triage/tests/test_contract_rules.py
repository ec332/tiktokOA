"""W0 contract C7: UI_ERROR is a product-side origin, and evidence gathered under an injected
dependency fault is not environment evidence (docs/iteration3/W0-contracts.md)."""

from tp.stages.triage import rules
from tp.stages.triage.tests.test_triage import rows_for, vd

FAULT = {"standin": "hook", "mode": "delay", "value": 30}


def _gateway(rows: dict, status: int, fault: dict | None) -> dict:
    for r in rows.values():
        r["exchanges"][0]["status"] = status
        r["fault"] = fault
    return rows


def test_a_ui_error_is_a_product_defect():
    v = vd("TC-U1")
    assert rules.classify(v, None, rows_for(v, origin="UI_ERROR"))[0] == "PRODUCT_DEFECT"


def test_a_gateway_status_is_the_environment_without_a_fault_and_the_product_under_one():
    v = vd("TC-G1")
    plain = _gateway(rows_for(v, origin="ASSERTION"), 503, None)
    assert rules.classify(v, None, plain)[0] == "ENV"
    faulted = _gateway(rows_for(v, origin="ASSERTION"), 503, FAULT)
    assert rules.classify(v, None, faulted)[0] == "PRODUCT_DEFECT"


def test_a_timeout_under_an_injected_delay_is_the_product_failing_to_absorb_it():
    v = vd("TC-G2")
    assert rules.classify(v, None, rows_for(v, origin="ENV"))[0] == "ENV"
    faulted = rows_for(v, origin="ENV")
    for r in faulted.values():
        r["fault"] = FAULT
    assert rules.classify(v, None, faulted)[0] == "PRODUCT_DEFECT"


def test_an_unhealthy_batch_is_still_the_environment_under_a_fault():
    v = vd("TC-G3", verdict="SUSPECT_ENV")
    faulted = rows_for(v, origin="ENV")
    for r in faulted.values():
        r["fault"] = FAULT
    assert rules.classify(v, None, faulted)[0] == "ENV"
