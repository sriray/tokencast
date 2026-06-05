import types

import pytest
import yaml

from optimize import cli


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


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                generate=2, min_quality=None, by="cost", out=str(tmp_path / "runs"),
                history=str(tmp_path / "no_history"), yes=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _fake_result():
    return [{"task_id": "t1", "by": "cost", "floor": 1.0,
             "monolithic": {"label": "monolithic", "composite": 1.0, "cost_usd": 0.02,
                            "duration_ms": 100, "passed": True, "steps": [], "note": ""},
             "strategies": [{"label": "monolithic", "cost_usd": 0.02, "duration_ms": 100},
                            {"label": "decomp-1", "cost_usd": 0.01, "duration_ms": 90}],
             "winner_label": "decomp-1", "cost_delta_pct": -50.0, "time_delta_pct": -10.0}]


def test_cmd_decompose_passes_through_and_prints(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_decompose(evalset, config, **k):
        captured.update(k)
        return _fake_result()

    monkeypatch.setattr(cli, "run_decompose", fake_run_decompose)
    cli.cmd_decompose(_args(tmp_path, generate=3, by="time", min_quality=0.7))
    assert captured["n"] == 3
    assert captured["by"] == "time"
    assert captured["min_quality"] == 0.7
    out = capsys.readouterr().out
    assert "t1" in out and "decomp-1" in out


def test_cmd_decompose_missing_evalset(tmp_path):
    with pytest.raises(SystemExit):
        cli.cmd_decompose(_args(tmp_path, evalset=str(tmp_path / "nope.yaml")))
