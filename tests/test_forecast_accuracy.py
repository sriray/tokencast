import json
import os
import types

import tokencast


def _write(path, marked, out=500, files=("a.py",)):
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}
    if marked:
        line["tokencast_accurate"] = True
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _write_many(d, n, marked):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        _write(os.path.join(d, f"s{i}.jsonl"), marked, out=500 + i * 10)


def test_parse_session_marker_sets_accurate(tmp_path):
    p = tmp_path / "m.jsonl"
    _write(str(p), marked=True)
    assert tokencast.parse_session(str(p))["accurate"] is True


def test_parse_session_no_marker_is_floor(tmp_path):
    p = tmp_path / "u.jsonl"
    _write(str(p), marked=False)
    assert tokencast.parse_session(str(p))["accurate"] is False


def _args(path, runs, **kw):
    base = dict(path=path, runs=runs, files=8, tools=30, output=None, count=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_dedup_sessions_collapses_by_project_session():
    s1 = {"project": "p", "session": "a"}
    s2 = {"project": "p", "session": "a"}
    s3 = {"project": "p", "session": "b"}
    out = tokencast._dedup_sessions([s1, s2, s3])
    assert [s["session"] for s in out] == ["a", "b"]


def test_forecast_prefers_accurate(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_many(str(runs), 6, marked=True)
    hist = tmp_path / "hist"
    os.makedirs(hist)
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "Calibrated on" in out
    assert "FLOOR" not in out


def test_forecast_floor_when_no_accurate(tmp_path, capsys):
    hist = tmp_path / "hist"
    _write_many(str(hist), 6, marked=False)
    runs = tmp_path / "none"   # does not exist
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "FLOOR" in out
    assert "Calibrated on" not in out


def test_forecast_dedups_when_path_equals_runs(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_many(str(runs), 6, marked=True)
    tokencast.cmd_forecast(_args(str(runs), str(runs)))   # path == runs
    out = capsys.readouterr().out
    assert "(of 6)" in out      # 6 unique accurate sessions, not 12
