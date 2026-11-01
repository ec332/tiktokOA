import pytest

from tp.core import labels


def test_children_inherit_parent_labels():
    assert labels.inherit([["unsigned"], ["recorded"]], ["simulated"]) == [
        "recorded",
        "simulated",
        "unsigned",
    ]


def test_unknown_label():
    with pytest.raises(ValueError):
        labels.inherit([], ["nope"])
