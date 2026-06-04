import pytest

from optimize.evalset import Check, Dimension


def test_check_from_dict_command():
    c = Check.from_dict({"kind": "command", "cmd": "pytest", "expect_exit": 0})
    assert c.kind == "command"
    assert c.cmd == "pytest"
    assert c.expect_exit == 0


def test_check_from_dict_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown check kind"):
        Check.from_dict({"kind": "nope"})


def test_check_from_dict_requires_fields():
    with pytest.raises(ValueError, match="cmd"):
        Check.from_dict({"kind": "command"})
    with pytest.raises(ValueError, match="path"):
        Check.from_dict({"kind": "file_exists"})
    with pytest.raises(ValueError, match="pattern"):
        Check.from_dict({"kind": "file_contains", "path": "a.py"})


def test_dimension_rule_single_and_list():
    d1 = Dimension.from_dict({"name": "t", "weight": 2,
                              "rule": {"kind": "file_exists", "path": "a.py"}})
    assert d1.is_rule and len(d1.checks) == 1 and d1.weight == 2.0
    d2 = Dimension.from_dict({"name": "t", "rule": [
        {"kind": "file_exists", "path": "a.py"},
        {"kind": "file_exists", "path": "b.py"}]})
    assert len(d2.checks) == 2


def test_dimension_judge():
    d = Dimension.from_dict({"name": "clarity", "weight": 2, "judge": "Is it clear? 0-1"})
    assert not d.is_rule
    assert d.judge == "Is it clear? 0-1"


def test_dimension_requires_exactly_one_of_rule_or_judge():
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x"})
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x", "rule": {"kind": "file_exists", "path": "a"},
                             "judge": "y"})


def test_dimension_required_on_judge_warns():
    with pytest.warns(UserWarning, match="required"):
        Dimension.from_dict({"name": "x", "required": True, "judge": "y"})
