import json
from types import SimpleNamespace

from optimize import cli
from optimize.result import RunResult


def _write_session(path, output_tokens):
    rec = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
           "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                       "content": [{"type": "text", "text": "x"}],
                       "usage": {"input_tokens": 0, "output_tokens": output_tokens,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 100000}}}
    path.write_text(json.dumps(rec) + "\n")


def test_estimate_cost_returns_p90_with_enough_history(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    for i in range(6):
        _write_session(proj / f"s{i}.jsonl", output_tokens=1000 * (i + 1))
    n, p90 = cli.estimate_cost(str(tmp_path))
    assert n == 6
    assert p90 is not None and p90 > 0


def test_estimate_cost_too_little_history(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_session(proj / "s0.jsonl", output_tokens=1000)
    n, p90 = cli.estimate_cost(str(tmp_path))
    assert n == 1
    assert p90 is None


def test_cmd_run_writes_jsonl_and_summary(tmp_path, monkeypatch, capsys):
    # Config dir
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    # Task file
    taskfile = tmp_path / "task1.md"
    taskfile.write_text("do the thing")
    out_dir = tmp_path / "runs"

    canned = RunResult(
        task_id="task1", config_id="baseline",
        model_usage={"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                           "cache_creation_input_tokens": 0,
                                           "cache_read_input_tokens": 10000}},
        cost_usd=0.0135, duration_ms=1000, num_turns=1,
        transcript=[{"model": "claude-sonnet-4-6", "content": []}],
        final_output="done", files_changed=[], accurate=True)

    monkeypatch.setattr(cli, "run_task", lambda task, config: canned)

    args = SimpleNamespace(
        taskfile=str(taskfile),
        config=str(cfg_dir),
        cwd=None,
        budget=1.0,
        history=str(tmp_path / "no_history"),
        out=str(out_dir),
        yes=True,
    )

    cli.cmd_run(args)

    out_file = out_dir / "task1-baseline.jsonl"
    assert out_file.exists()
    captured = capsys.readouterr()
    assert "$0.01" in captured.out  # cost summary printed


import types

import pytest


def _basic_config(tmp_path):
    cfg = tmp_path / "baseline"
    cfg.mkdir()
    (cfg / "metadata.yaml").write_text("model: sonnet\n")
    return cfg


def test_cmd_run_rejects_missing_config(tmp_path):
    taskfile = tmp_path / "t.md"
    taskfile.write_text("x")
    args = types.SimpleNamespace(
        taskfile=str(taskfile), config=str(tmp_path / "does_not_exist"),
        cwd=None, budget=None, history=str(tmp_path / "no_history"),
        out=str(tmp_path / "runs"), yes=True)
    with pytest.raises(SystemExit):
        cli.cmd_run(args)
    assert not (tmp_path / "runs").exists()  # bailed before any run/output


def test_cmd_run_rejects_missing_taskfile(tmp_path):
    cfg = _basic_config(tmp_path)
    args = types.SimpleNamespace(
        taskfile=str(tmp_path / "missing.md"), config=str(cfg),
        cwd=None, budget=None, history=str(tmp_path / "no_history"),
        out=str(tmp_path / "runs"), yes=True)
    with pytest.raises(SystemExit):
        cli.cmd_run(args)


def test_subcent_cost_is_not_displayed_as_zero(tmp_path, monkeypatch, capsys):
    cfg = _basic_config(tmp_path)
    taskfile = tmp_path / "t.md"
    taskfile.write_text("x")
    canned = RunResult(
        task_id="t", config_id="baseline",
        model_usage={"claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 50,
                                           "cache_creation_input_tokens": 0,
                                           "cache_read_input_tokens": 0}},
        cost_usd=0.0049, duration_ms=500, num_turns=1,
        transcript=[{"model": "claude-sonnet-4-6", "content": []}],
        final_output="d", files_changed=[], accurate=True)
    monkeypatch.setattr(cli, "run_task", lambda task, config: canned)
    args = types.SimpleNamespace(
        taskfile=str(taskfile), config=str(cfg), cwd=None, budget=None,
        history=str(tmp_path / "no_history"), out=str(tmp_path / "runs"), yes=True)
    cli.cmd_run(args)
    out = capsys.readouterr().out
    assert "$0.0049" in out          # precise, not collapsed to $0.00
    assert "$0.00 " not in out       # the misleading rounding must not appear
