import json

import yaml

from optimize import cli
from optimize.scorer import EvalReport, TaskScore


def _config(tmp_path):
    cfg = tmp_path / "baseline"
    cfg.mkdir()
    (cfg / "metadata.yaml").write_text("model: sonnet\n")
    return cfg


def test_cmd_eval_run_writes_and_prints(tmp_path, monkeypatch, capsys):
    cfg = _config(tmp_path)
    evalset_path = tmp_path / "evalset.yaml"
    evalset_path.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))

    canned = EvalReport.from_scores("baseline", [
        TaskScore("t1", {"d": 0.9}, composite=0.9, passed=True, cost_usd=0.0049,
                  duration_ms=1500)])
    monkeypatch.setattr(cli, "run_evalset", lambda evalset, config, out_dir: canned)

    import types
    args = types.SimpleNamespace(evalset=str(evalset_path), config=str(cfg),
                                 out=str(tmp_path / "runs"),
                                 history=str(tmp_path / "no_history"), yes=True)
    cli.cmd_eval_run(args)
    out = capsys.readouterr().out
    assert "Composite" in out
    assert "$0.0049" in out
    assert "90%" in out


def test_cmd_eval_run_rejects_missing_config(tmp_path):
    import pytest
    import types
    evalset_path = tmp_path / "evalset.yaml"
    evalset_path.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))
    args = types.SimpleNamespace(evalset=str(evalset_path),
                                 config=str(tmp_path / "nope"),
                                 out=str(tmp_path / "runs"),
                                 history=str(tmp_path / "no_history"), yes=True)
    with pytest.raises(SystemExit):
        cli.cmd_eval_run(args)


def test_cmd_eval_init_writes_draft(tmp_path, monkeypatch, capsys):
    from optimize.evalset import EvalSet
    canned = EvalSet.from_dict({"tasks": [
        {"id": "g1", "prompt": "do x", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]})
    monkeypatch.setattr(cli, "generate_evalset", lambda context: canned)

    import types
    out_dir = tmp_path / "evals" / "generated"
    args = types.SimpleNamespace(root=str(tmp_path), out=str(out_dir))
    cli.cmd_eval_init(args)

    written = out_dir / "evalset.yaml"
    assert written.exists()
    loaded = EvalSet.load(str(written))
    assert loaded.tasks[0].id == "g1"
    err = capsys.readouterr().err
    assert "REVIEW" in err.upper()
