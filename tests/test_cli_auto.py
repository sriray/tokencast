import types

import pytest
import yaml

from optimize import cli
from optimize.ranking import CandidateResult, OptimizeResult


def _evalset_file(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]}))
    return p


def _config_dir(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return d


def _cr(cid, quality, cost):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=1.0, cost_usd=cost,
                           duration_ms=1000.0, repeats=1, cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality, runway=None, fits=None)


def _result():
    return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                          improved=False, candidates=[_cr("baseline", 0.9, 0.02)],
                          pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                candidate=None, model_sweep=False, repeats=1, min_quality=None, by="cost",
                out=str(tmp_path / "runs"), promote=None,
                history=str(tmp_path / "no_history"), yes=True,
                budget_remaining=None, budget_config=None, budget_scope="global",
                need_tasks=None, generate=0, skills_dir=None, mcp_catalog=None, decompose=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_auto_passes_through_and_prints(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_auto(baseline, evalset, **k):
        captured.update(k)
        return {"winner_id": "baseline", "optimize": _result(), "decompose": None}

    monkeypatch.setattr(cli, "run_auto", fake_run_auto)
    cli.cmd_auto(_args(tmp_path, generate=2, decompose=True, by="time", min_quality=0.5))
    assert captured["with_decompose"] is True
    assert captured["n_generated"] == 2
    assert captured["by"] == "time"
    assert captured["min_quality"] == 0.5
    out = capsys.readouterr().out
    assert "Winner: baseline" in out
    assert "auto.json" in out


def test_cmd_auto_missing_evalset(tmp_path):
    with pytest.raises(SystemExit):
        cli.cmd_auto(_args(tmp_path, evalset=str(tmp_path / "nope.yaml")))
