"""Tests for the post-ship review fixes: crashers, hardening, and honesty.

Each test was written before its fix and watched fail (TDD). They lock in:
  C1  negative --files/--tools/--output are rejected, not crashed on (log1p)
  C2  report survives a zero-total-cost pool (no ZeroDivisionError)
  G   _dedup_sessions prefers the accurate copy on a key collision
  G   budget config rejects non-finite (NaN/Infinity) caps
  E   RunResult captures the SDK's authoritative provider cost
  D   estimate flags hintless tickets that collapse to one shared number
"""
import json
import os
import types

import pytest

import budget
import tokencast
from optimize.result import RunResult


def _write_session(d, name, out=500, files=("a.py",), cache_read=50000):
    os.makedirs(d, exist_ok=True)
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": cache_read}}}
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _fargs(path, runs, **kw):
    base = dict(path=path, runs=runs, files=8, tools=30, output=None, count=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --- C1: negative size flags are rejected up front, not crashed on ----------
def test_forecast_rejects_negative_tools(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_forecast(_fargs(str(tmp_path / "h"), str(tmp_path / "r"), tools=-3))


def test_forecast_rejects_negative_files(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_forecast(_fargs(str(tmp_path / "h"), str(tmp_path / "r"), files=-1))


def test_estimate_rejects_negative_output(tmp_path):
    plan = tmp_path / "p.md"
    plan.write_text("- one ticket\n", encoding="utf-8")
    args = types.SimpleNamespace(plan=str(plan), path=str(tmp_path / "h"),
                                 runs=str(tmp_path / "r"), files=None, tools=None,
                                 output=-5, refresh_prices=False)
    with pytest.raises(SystemExit):
        tokencast.cmd_estimate(args)


# --- C2: report survives a zero-total-cost pool -----------------------------
def test_report_survives_zero_total_cost(tmp_path, capsys):
    p = tmp_path / "z.jsonl"
    p.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z",
                             "model": "claude-sonnet-4-6",
                             "usage": {"input_tokens": 0, "output_tokens": 0,
                                       "cache_creation_input_tokens": 0,
                                       "cache_read_input_tokens": 0}}) + "\n",
                 encoding="utf-8")
    args = types.SimpleNamespace(path=str(p), cap=None, segment=False, gap_min=30,
                                 split_on_user=False, format="generic",
                                 refresh_prices=False)
    tokencast.cmd_report(args)  # must not raise
    out = capsys.readouterr().out
    assert "By project" in out


# --- G: dedup prefers the accurate copy on a key collision ------------------
def test_dedup_prefers_accurate_copy():
    inacc = {"project": "p", "session": "s", "accurate": False}
    acc = {"project": "p", "session": "s", "accurate": True}
    out = tokencast._dedup_sessions([inacc, acc])
    assert len(out) == 1
    assert out[0]["accurate"] is True


# --- G: budget rejects non-finite caps --------------------------------------
def test_budget_rejects_nonfinite_amount(tmp_path):
    cfg = tmp_path / "b.json"
    cfg.write_text('{"period":"quarterly","period_start":"2026-04-01",'
                   '"budgets":[{"scope":"global","amount":NaN}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        budget.BudgetConfig.load(str(cfg))


# --- E: the SDK's authoritative cost is captured, not discarded -------------
_RAW = {
    "turns": [{"model": "claude-sonnet-4-6", "content": []}],
    "result": {"model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000,
                                                      "output_tokens": 500,
                                                      "cache_creation_input_tokens": 0,
                                                      "cache_read_input_tokens": 10000}},
               "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.013,
               "result_text": "done"},
}


def test_runresult_captures_provider_cost():
    rr = RunResult.from_raw(_RAW, task_id="t", config_id="c")
    assert rr.provider_cost_usd == 0.013


# --- D: estimate flags hintless tickets that share one estimate -------------
def _estimate_args(plan, hist, tmp_path):
    return types.SimpleNamespace(plan=str(plan), path=str(hist), runs=str(tmp_path / "r"),
                                 files=None, tools=None, output=None, refresh_prices=False)


def test_estimate_warns_on_hintless_tickets(tmp_path, capsys):
    hist = tmp_path / "h"
    for i in range(8):
        _write_session(str(hist), f"s{i}.jsonl", out=500 + i * 30, cache_read=50000 + i * 4000)
    plan = tmp_path / "p.md"
    plan.write_text("- alpha task\n- beta task\n", encoding="utf-8")
    tokencast.cmd_estimate(_estimate_args(plan, hist, tmp_path))
    out = capsys.readouterr().out.lower()
    assert "had no size hint" in out


def test_estimate_no_warning_when_all_hinted(tmp_path, capsys):
    hist = tmp_path / "h"
    for i in range(8):
        _write_session(str(hist), f"s{i}.jsonl", out=500 + i * 30, cache_read=50000 + i * 4000)
    plan = tmp_path / "p.md"
    plan.write_text("- alpha (files=3 tools=5)\n- beta (files=9 tools=20)\n", encoding="utf-8")
    tokencast.cmd_estimate(_estimate_args(plan, hist, tmp_path))
    out = capsys.readouterr().out.lower()
    assert "had no size hint" not in out
