import json

import pytest

from tp.core import artifacts


def test_put_same_twice_is_noop():
    a = artifacts.put("cases", {"x": 1})
    b = artifacts.put("cases", {"x": 1})
    assert a == b
    assert len(artifacts.list_()) == 1


def test_lineage_is_part_of_identity():
    p = artifacts.put("spec", {"s": 1})
    a = artifacts.put("cases", {"x": 1})
    b = artifacts.put("cases", {"x": 1}, parents=[p.id])
    assert a.id != b.id
    assert [c["id"] for c in artifacts.children(p.id)] == [b.id]


def test_different_body_under_existing_id_raises(data):
    a = artifacts.put("cases", {"x": 1})
    path = data / "artifacts" / (a.id.removeprefix("sha256:") + ".json")
    stored = json.loads(path.read_text())
    stored["body"] = {"x": 2}
    path.write_text(json.dumps(stored))
    with pytest.raises(artifacts.ArtifactConflict):
        artifacts.put("cases", {"x": 1})
    with pytest.raises(artifacts.ArtifactConflict):
        artifacts.get(a.id)


def test_unknown_label_rejected():
    with pytest.raises(ValueError):
        artifacts.put("cases", {}, labels=["vibes"])
