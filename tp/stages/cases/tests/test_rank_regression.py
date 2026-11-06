"""rank() regression (seams/001 A1): the configurable priority score with default weights gives
exactly today's order, scores and labels on r-0002's cases. The fixture was frozen from rank() at
7b1d942 (r-0002 itself predates the feature-first change in b973dc1, so its stored order is older).
"""

import json
from pathlib import Path

import pytest

from tp.stages.cases import build

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "rank_r0002.json").read_text())


@pytest.mark.parametrize("variant", sorted(FIXTURE["expected"]))
def test_rank_reproduces_r0002(variant):
    units = [dict(u) for u in FIXTURE["units"]]
    build.rank(units, FIXTURE["criteria"], FIXTURE["findings"], FIXTURE["variants"][variant])
    got = [[u["ac"], u["technique"], u["score"], u["priority"]] for u in units]
    assert got == FIXTURE["expected"][variant]
