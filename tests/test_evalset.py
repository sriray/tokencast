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


def test_check_to_dict_preserves_timeout_and_expect_exit():
    c = Check.from_dict({"kind": "command", "cmd": "pytest", "timeout": 300,
                         "expect_exit": 2})
    d = c.to_dict()
    assert d["timeout"] == 300
    assert d["expect_exit"] == 2
    # round-trips
    c2 = Check.from_dict(d)
    assert c2.timeout == 300 and c2.expect_exit == 2


def test_check_to_dict_omits_default_timeout():
    d = Check.from_dict({"kind": "command", "cmd": "pytest"}).to_dict()
    assert "timeout" not in d  # default not serialized


def test_dimension_rejects_null_rule_or_judge():
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x", "judge": None})
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x", "rule": None})


def test_dimension_rejects_empty_rule():
    with pytest.raises(ValueError, match="at least one check"):
        Dimension.from_dict({"name": "x", "rule": []})


def test_dimension_rejects_negative_weight():
    with pytest.raises(ValueError, match="weight"):
        Dimension.from_dict({"name": "x", "weight": -1, "judge": "y"})


from optimize.evalset import EvalSet, EvalTask


def test_evaltask_from_dict_minimal():
    t = EvalTask.from_dict({"id": "t1", "prompt": "do it",
                            "dimensions": [{"name": "d", "judge": "good? 0-1"}]})
    assert t.id == "t1"
    assert t.pass_threshold == 0.6
    assert len(t.dimensions) == 1


def test_evaltask_requires_id_and_prompt():
    with pytest.raises(ValueError, match="id"):
        EvalTask.from_dict({"prompt": "x"})
    with pytest.raises(ValueError, match="prompt"):
        EvalTask.from_dict({"id": "t"})


def test_evaltask_rejects_both_seed_modes():
    with pytest.raises(ValueError, match="mutually exclusive"):
        EvalTask.from_dict({"id": "t", "prompt": "p", "seed_dir": "s",
                            "seed_repo": {"path": "r"}})


def test_evalset_load_roundtrip(tmp_path):
    src = {
        "tasks": [
            {"id": "t1", "prompt": "p1", "pass_threshold": 0.5,
             "dimensions": [
                 {"name": "tests", "weight": 3, "required": True,
                  "rule": {"kind": "command", "cmd": "python x.py", "expect_exit": 0}},
                 {"name": "clarity", "weight": 2, "judge": "clear? 0-1"}]},
        ]
    }
    import yaml
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump(src))

    es = EvalSet.load(str(p))
    assert len(es.tasks) == 1
    t = es.tasks[0]
    assert t.pass_threshold == 0.5
    assert t.dimensions[0].required is True
    assert t.dimensions[0].checks[0].cmd == "python x.py"
    assert t.dimensions[1].judge == "clear? 0-1"

    out = tmp_path / "out.yaml"
    es.save(str(out))
    es2 = EvalSet.load(str(out))
    assert es2.tasks[0].dimensions[0].checks[0].cmd == "python x.py"
    assert es2.tasks[0].dimensions[1].judge == "clear? 0-1"


def test_evalset_load_rejects_empty(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text("tasks: []\n")
    with pytest.raises(ValueError, match="no tasks"):
        EvalSet.load(str(p))


def test_evalset_load_wraps_bad_yaml(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text("tasks: [ : bad")
    with pytest.raises(ValueError, match="evalset.yaml"):
        EvalSet.load(str(p))


def test_evalset_single_task_is_fine(tmp_path):
    import yaml
    p = tmp_path / "one.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "solo", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))
    es = EvalSet.load(str(p))
    assert len(es.tasks) == 1


def test_evaltask_rejects_seed_repo_without_path():
    with pytest.raises(ValueError, match="seed_repo requires 'path'"):
        EvalTask.from_dict({"id": "t", "prompt": "p", "seed_repo": {"ref": "HEAD"},
                            "dimensions": [{"name": "d", "judge": "ok? 0-1"}]})


def test_evaltask_rejects_out_of_range_threshold():
    with pytest.raises(ValueError, match="pass_threshold"):
        EvalTask.from_dict({"id": "t", "prompt": "p", "pass_threshold": 5,
                            "dimensions": [{"name": "d", "judge": "ok? 0-1"}]})


def test_evaltask_rejects_zero_total_weight():
    # A task with dimensions whose weights all sum to 0 would make the scorer's
    # composite collapse to 0.0 regardless of dimension scores — reject it loudly.
    with pytest.raises(ValueError, match="dimension weights sum to 0"):
        EvalTask.from_dict({"id": "t", "prompt": "p", "dimensions": [
            {"name": "a", "weight": 0, "judge": "ok? 0-1"},
            {"name": "b", "weight": 0, "judge": "ok? 0-1"}]})


def test_evalset_rejects_duplicate_task_ids():
    with pytest.raises(ValueError, match="duplicate task id"):
        EvalSet.from_dict({"tasks": [
            {"id": "dup", "prompt": "a", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]},
            {"id": "dup", "prompt": "b", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]})
