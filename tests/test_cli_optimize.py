import types

import yaml

from optimize import cli
from optimize.ranking import CandidateResult, OptimizeResult


def _evalset_file(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok? 0-1"}]}]}))
    return p


def _config_dir(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return d


def _cr(cid, quality, cost, runway=None, fits=None):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=1.0, cost_usd=cost,
                           duration_ms=1000.0, repeats=1, cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality, runway=runway, fits=fits)


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                candidate=None, model_sweep=True, repeats=1, min_quality=None, by="cost",
                out=str(tmp_path / "runs"), promote=None,
                history=str(tmp_path / "no_history"), yes=True,
                budget_remaining=None, budget_config=None, budget_scope="global",
                need_tasks=None, generate=0)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_optimize_prints_ranked_table(tmp_path, monkeypatch, capsys):
    canned = OptimizeResult(
        baseline_id="baseline", floor=0.8, winner_id="baseline-haiku", improved=True,
        candidates=[_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)],
        pareto=["baseline-haiku"], cost_delta_pct=-50.0, quality_delta=0.0)
    monkeypatch.setattr(cli, "run_optimize", lambda *a, **k: canned)
    cli.cmd_optimize(_args(tmp_path))
    out = capsys.readouterr().out
    assert "baseline-haiku" in out
    assert "*" in out
    assert "-50%" in out


def test_cmd_optimize_with_budget_shows_runway(tmp_path, monkeypatch, capsys):
    canned = OptimizeResult(
        baseline_id="baseline", floor=0.8, winner_id="baseline-haiku", improved=True,
        candidates=[_cr("baseline", 0.8, 0.020, runway=500),
                    _cr("baseline-haiku", 0.8, 0.010, runway=1000)],
        pareto=["baseline-haiku"], cost_delta_pct=-50.0, quality_delta=0.0,
        budget_remaining=10.0, runway_gain=500)
    monkeypatch.setattr(cli, "run_optimize", lambda *a, **k: canned)
    cli.cmd_optimize(_args(tmp_path, budget_remaining=10.0))
    out = capsys.readouterr().out
    assert "runway" in out.lower()
    assert "+500" in out


def test_cmd_optimize_need_tasks_without_budget_errors(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        cli.cmd_optimize(_args(tmp_path, need_tasks=100))


def test_cmd_optimize_rejects_missing_config(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        cli.cmd_optimize(_args(tmp_path, config=str(tmp_path / "nope")))


def test_cmd_optimize_renders_need_tasks_fit_verdict(tmp_path, monkeypatch, capsys):
    canned = OptimizeResult(
        baseline_id="baseline", floor=0.8, winner_id="baseline-haiku", improved=True,
        candidates=[_cr("baseline", 0.8, 0.020, runway=500, fits=False),
                    _cr("baseline-haiku", 0.8, 0.010, runway=1000, fits=True)],
        pareto=["baseline-haiku"], cost_delta_pct=-50.0, quality_delta=0.0,
        budget_remaining=10.0, need_tasks=800, runway_gain=500, winner_fits=True)
    monkeypatch.setattr(cli, "run_optimize", lambda *a, **k: canned)
    cli.cmd_optimize(_args(tmp_path, budget_remaining=10.0, need_tasks=800))
    out = capsys.readouterr().out
    assert "1000t" in out                    # runway column cell rendered
    assert "fit" in out                      # per-candidate fit tag (fit / !fit)
    assert "Need 800 tasks" in out           # fit-verdict line
    assert "winner fits: YES" in out         # winner_fits=True -> YES


def test_cmd_optimize_passes_generate_through(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_optimize(*a, **k):
        captured.update(k)
        return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                              improved=False, candidates=[_cr("baseline", 0.8, 0.02)],
                              pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)

    monkeypatch.setattr(cli, "run_optimize", fake_run_optimize)
    cli.cmd_optimize(_args(tmp_path, generate=2, model_sweep=False))
    assert captured["n_generated"] == 2
    assert "baseline" in capsys.readouterr().out


def test_cmd_optimize_negative_generate_clamped(tmp_path, monkeypatch):
    captured = {}

    def fake_run_optimize(*a, **k):
        captured.update(k)
        return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                              improved=False, candidates=[_cr("baseline", 0.8, 0.02)],
                              pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)

    monkeypatch.setattr(cli, "run_optimize", fake_run_optimize)
    cli.cmd_optimize(_args(tmp_path, generate=-5, model_sweep=False))
    assert captured["n_generated"] == 0   # negative clamped to 0
