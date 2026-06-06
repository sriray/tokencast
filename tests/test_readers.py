"""Tests for ROADMAP #4: multi-agent readers.

A reader maps ONE log file to the SAME session-summary dicts parse_session produces.
The forecast/report layer is agent-agnostic; only the reader differs. These tests pin:
  1. the claude-code reader == today's parse_session (no regression),
  2. the generic reader reduces a generic-schema file to the contract with correct cost,
  3. --format auto picks the right reader per file,
  4. unknown/empty/foreign files degrade gracefully (skipped, no crash),
  5. default forecast/report (no --format) is unchanged vs a direct load().
"""
import io
import json
import types
from contextlib import redirect_stdout

import tokencast


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------
def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(
        tzinfo=None).isoformat() + "Z"


def _cc_assistant(epoch, out=500, model="claude-sonnet-4-6", file="a.py", inp=1):
    content = [{"type": "text", "text": "x"}]
    if file:
        content.append({"type": "tool_use", "name": "Edit", "input": {"file_path": file}})
    return {"type": "assistant", "timestamp": _iso(epoch),
            "message": {"role": "assistant", "model": model, "content": content,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 100000}}}


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _generic_msg(epoch, out=500, model="claude-sonnet-4-6", inp=1234,
                 cache_read=100000, tools=None, files=None, accurate=False):
    rec = {"timestamp": _iso(epoch), "model": model,
           "usage": {"input_tokens": inp, "output_tokens": out,
                     "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": cache_read}}
    if tools is not None:
        rec["tools"] = tools
    if files is not None:
        rec["files"] = files
    if accurate:
        rec["tokencast_accurate"] = True
    return rec


T0 = 1_748_000_000


# --------------------------------------------------------------------------------------
# 1. claude-code reader == today's parse_session
# --------------------------------------------------------------------------------------
def test_claude_code_reader_matches_parse_session(tmp_path):
    p = tmp_path / "proj" / "sess.jsonl"
    p.parent.mkdir()
    _write_jsonl(p, [{"type": "user", "timestamp": _iso(T0),
                      "message": {"role": "user", "content": "go"}},
                     _cc_assistant(T0 + 30),
                     _cc_assistant(T0 + 60, file="b.py")])

    direct = tokencast.parse_session(str(p))
    via_reader = tokencast.read_file(str(p), fmt="claude-code")
    assert len(via_reader) == 1
    assert via_reader[0] == direct  # byte-for-byte the same summary dict


# --------------------------------------------------------------------------------------
# 2. generic reader -> the contract, correct cost + features
# --------------------------------------------------------------------------------------
def test_generic_reader_reduces_to_contract(tmp_path):
    p = tmp_path / "myproj" / "run.jsonl"
    p.parent.mkdir()
    msgs = [
        _generic_msg(T0, out=1000, inp=1, tools=["Edit", "Bash"], files=["src/a.py"]),
        _generic_msg(T0 + 120, out=500, inp=1, files=["src/b.py", "src/a.py"]),
    ]
    _write_jsonl(p, msgs)

    out = tokencast.read_file(str(p), fmt="generic")
    assert len(out) == 1
    s = out[0]

    # Same keys + shapes as a Claude Code summary.
    cc = tokencast.parse_session(str(p))  # parse_session won't see these as turns
    assert set(s.keys()) >= set(cc.keys())
    assert s["session"] == "run"
    assert s["project"] == "myproj"
    assert s["assistant_turns"] == 2
    assert s["output"] == 1500
    assert s["cache_read"] == 200000
    assert s["tool_calls"] == 2          # Edit + Bash from message 1
    assert s["files_touched"] == 2       # src/a.py (deduped) + src/b.py
    assert s["models"] == {"claude-sonnet-4-6"}
    assert s["undercount_hits"] == 2     # input_tokens placeholder honesty preserved
    assert s["accurate"] is False
    assert s["duration_min"] is not None and s["duration_min"] > 0

    # Cost equals the shared entry_cost math summed over messages (one place for cost).
    expected = sum(tokencast.entry_cost(m["usage"], m["model"]) for m in msgs)
    assert abs(s["cost"] - expected) < 1e-9
    assert s["cost"] > 0


def test_generic_reader_accurate_marker(tmp_path):
    p = tmp_path / "proj" / "acc.jsonl"
    p.parent.mkdir()
    _write_jsonl(p, [_generic_msg(T0, inp=900, accurate=True)])
    s = tokencast.read_file(str(p), fmt="generic")[0]
    assert s["accurate"] is True
    assert s["undercount_hits"] == 0  # real input_tokens, not a placeholder


# --------------------------------------------------------------------------------------
# 3. --format auto picks the right reader per file
# --------------------------------------------------------------------------------------
def test_auto_detects_claude_code(tmp_path):
    p = tmp_path / "proj" / "cc.jsonl"
    p.parent.mkdir()
    _write_jsonl(p, [_cc_assistant(T0), _cc_assistant(T0 + 30)])
    assert tokencast._sniff_format(str(p)) == "claude-code"
    auto = tokencast.read_file(str(p), fmt="auto")
    assert auto == tokencast.read_file(str(p), fmt="claude-code")


def test_auto_detects_generic(tmp_path):
    p = tmp_path / "proj" / "gen.jsonl"
    p.parent.mkdir()
    _write_jsonl(p, [_generic_msg(T0), _generic_msg(T0 + 60)])
    assert tokencast._sniff_format(str(p)) == "generic"
    auto = tokencast.read_file(str(p), fmt="auto")
    assert len(auto) == 1 and auto[0]["assistant_turns"] == 2


def test_load_with_format_auto_mixed_dir(tmp_path):
    (tmp_path / "p").mkdir()
    _write_jsonl(tmp_path / "p" / "cc.jsonl", [_cc_assistant(T0), _cc_assistant(T0 + 30)])
    _write_jsonl(tmp_path / "p" / "gen.jsonl", [_generic_msg(T0), _generic_msg(T0 + 60)])
    sessions = tokencast.load_with_format(str(tmp_path), "auto")
    names = sorted(s["session"] for s in sessions)
    assert names == ["cc", "gen"]  # both formats read in one directory


# --------------------------------------------------------------------------------------
# 4. unknown / empty / foreign files degrade gracefully
# --------------------------------------------------------------------------------------
def test_empty_file_degrades(tmp_path):
    p = tmp_path / "proj" / "empty.jsonl"
    p.parent.mkdir()
    p.write_text("")
    # generic reader: nothing usable -> []
    assert tokencast.read_file(str(p), fmt="generic") == []
    # claude-code/auto returns a 0-turn summary (legacy parse_session shape); the loader
    # is what drops it, so the directory yields nothing -- no crash.
    assert tokencast.load_with_format(str(tmp_path), "auto") == []
    assert tokencast.load_with_format(str(tmp_path), "claude-code") == []


def test_foreign_file_degrades(tmp_path):
    p = tmp_path / "proj" / "foreign.jsonl"
    p.parent.mkdir()
    # Valid JSONL but neither schema: no usage, no message envelope.
    _write_jsonl(p, [{"hello": "world"}, {"foo": 1, "bar": [1, 2, 3]}])
    # generic reader: nothing usable -> []
    assert tokencast.read_file(str(p), fmt="generic") == []
    # auto: falls back to claude-code, which also yields a 0-turn summary -> filtered by load
    assert tokencast.load_with_format(str(tmp_path), "auto") == []


def test_unknown_format_returns_empty(tmp_path):
    p = tmp_path / "proj" / "x.jsonl"
    p.parent.mkdir()
    _write_jsonl(p, [_generic_msg(T0)])
    assert tokencast.read_file(str(p), fmt="does-not-exist") == []


def test_load_skips_zero_turn_summaries(tmp_path):
    (tmp_path / "p").mkdir()
    _write_jsonl(tmp_path / "p" / "good.jsonl", [_generic_msg(T0)])
    _write_jsonl(tmp_path / "p" / "junk.jsonl", [{"nope": 1}])
    sessions = tokencast.load_with_format(str(tmp_path), "auto")
    assert [s["session"] for s in sessions] == ["good"]


# --------------------------------------------------------------------------------------
# 5. backward-compat: default report/forecast (no --format) unchanged vs load()
# --------------------------------------------------------------------------------------
def _seed_cc_history(root, n=8):
    proj = root / "proj"
    proj.mkdir()
    for i in range(n):
        _write_jsonl(proj / f"s{i}.jsonl",
                     [{"type": "user", "timestamp": _iso(T0 + i * 86400),
                       "message": {"role": "user", "content": "go"}},
                      _cc_assistant(T0 + i * 86400 + 30, out=500 + 100 * i),
                      _cc_assistant(T0 + i * 86400 + 90, out=400 + 50 * i, file="b.py")])


def _run_cmd(func, args_ns):
    buf = io.StringIO()
    with redirect_stdout(buf):
        func(args_ns)
    return buf.getvalue()


def test_default_report_unchanged(tmp_path):
    _seed_cc_history(tmp_path)
    base = types.SimpleNamespace(path=str(tmp_path), cap=None, segment=False,
                                 gap_min=30, split_on_user=False, format="auto")
    explicit = types.SimpleNamespace(path=str(tmp_path), cap=None, segment=False,
                                     gap_min=30, split_on_user=False, format="claude-code")
    # auto (default) and explicit claude-code produce identical report output.
    assert _run_cmd(tokencast.cmd_report, base) == _run_cmd(tokencast.cmd_report, explicit)


def test_default_forecast_unchanged(tmp_path):
    _seed_cc_history(tmp_path)
    common = dict(path=str(tmp_path), runs=str(tmp_path / "no_runs"),
                  files=2, tools=4, output=None, count=None,
                  segment=False, gap_min=30, split_on_user=False)
    base = types.SimpleNamespace(format="auto", **common)
    explicit = types.SimpleNamespace(format="claude-code", **common)
    out_auto = _run_cmd(tokencast.cmd_forecast, base)
    out_cc = _run_cmd(tokencast.cmd_forecast, explicit)
    assert out_auto == out_cc
    assert "task estimate" in out_auto.lower()
