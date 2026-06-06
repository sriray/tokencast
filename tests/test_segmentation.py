"""Tests for ROADMAP #2: task segmentation (idle-gap + optional user-turn boundaries)."""
import json
import os
import types

import tokencast


# --------------------------------------------------------------------------------------
# Helpers: build raw JSONL transcripts with controllable timestamps.
# --------------------------------------------------------------------------------------
def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(
        tzinfo=None).isoformat() + "Z"


def _assistant(epoch, out=500, file="a.py"):
    content = [{"type": "text", "text": "x"}]
    if file:
        content.append({"type": "tool_use", "name": "Edit", "input": {"file_path": file}})
    return {"type": "assistant", "timestamp": _iso(epoch),
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}


def _user(epoch, text="..."):
    return {"type": "user", "timestamp": _iso(epoch),
            "message": {"role": "user", "content": text}}


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


T0 = 1_748_000_000
MIN = 60  # seconds in a minute


# --------------------------------------------------------------------------------------
# segment_entries (pure)
# --------------------------------------------------------------------------------------
def test_big_idle_gap_splits_into_two():
    entries = [
        _user(T0), _assistant(T0 + 30), _assistant(T0 + 60),
        # 45-minute idle gap -> split
        _user(T0 + 60 + 45 * MIN), _assistant(T0 + 60 + 45 * MIN + 30),
    ]
    segs = tokencast.segment_entries(entries, gap_min=30, split_on_user=False)
    assert len(segs) == 2
    assert len(segs[0]) == 3
    assert len(segs[1]) == 2


def test_small_gap_stays_one():
    entries = [
        _user(T0), _assistant(T0 + 30), _assistant(T0 + 60),
        _user(T0 + 60 + 10 * MIN), _assistant(T0 + 60 + 10 * MIN + 30),
    ]
    segs = tokencast.segment_entries(entries, gap_min=30, split_on_user=False)
    assert len(segs) == 1


def test_gap_min_zero_disables_gap_rule():
    entries = [
        _user(T0), _assistant(T0 + 30),
        _user(T0 + 10 * 3600), _assistant(T0 + 10 * 3600 + 30),  # 10h gap
    ]
    segs = tokencast.segment_entries(entries, gap_min=0, split_on_user=False)
    assert len(segs) == 1


def test_entries_without_timestamps_attach_to_current_segment():
    entries = [
        _user(T0), _assistant(T0 + 30),
        {"type": "system"},  # no timestamp
        _assistant(T0 + 60),
        _user(T0 + 60 + 40 * MIN), _assistant(T0 + 60 + 40 * MIN + 30),
    ]
    segs = tokencast.segment_entries(entries, gap_min=30, split_on_user=False)
    assert len(segs) == 2
    assert {"type": "system"} in segs[0]


def test_split_on_user_cuts_new_task_but_not_leading_prompt():
    # Two tasks, no idle gap, but a fresh user turn after the first task completes.
    entries = [
        _user(T0), _assistant(T0 + 30), _assistant(T0 + 60),
        _user(T0 + 90),  # new task -> split here (segment 1 already has assistant usage)
        _assistant(T0 + 120),
    ]
    segs = tokencast.segment_entries(entries, gap_min=30, split_on_user=True)
    assert len(segs) == 2
    # The leading user prompt did not create an empty segment.
    assert segs[0][0]["type"] == "user"
    assert len(segs[0]) == 3


def test_split_on_user_off_by_default():
    entries = [
        _user(T0), _assistant(T0 + 30),
        _user(T0 + 60), _assistant(T0 + 90),
    ]
    segs = tokencast.segment_entries(entries, gap_min=30, split_on_user=False)
    assert len(segs) == 1


# --------------------------------------------------------------------------------------
# parse_session_segments + additive invariants
# --------------------------------------------------------------------------------------
def test_parse_session_segments_splits_and_is_additive(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(str(p), [
        _user(T0), _assistant(T0 + 30, out=400, file="a.py"),
        _assistant(T0 + 60, out=600, file="b.py"),
        _user(T0 + 60 + 45 * MIN), _assistant(T0 + 60 + 45 * MIN + 30, out=700, file="c.py"),
    ])
    segs = tokencast.parse_session_segments(str(p), gap_min=30, split_on_user=False)
    whole = tokencast.parse_session(str(p))
    assert len(segs) == 2
    for field in ("cost", "output", "tool_calls", "assistant_turns"):
        assert abs(sum(s[field] for s in segs) - whole[field]) < 1e-9
    # ids suffixed when split
    assert [s["session"] for s in segs] == ["s#1", "s#2"]


def test_single_segment_keeps_bare_id(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(str(p), [_user(T0), _assistant(T0 + 30)])
    segs = tokencast.parse_session_segments(str(p), gap_min=30, split_on_user=False)
    assert len(segs) == 1
    assert segs[0]["session"] == "s"


def test_trailing_idle_user_only_segment_is_dropped(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(str(p), [
        _user(T0), _assistant(T0 + 30),
        # big gap then only a user line, no assistant usage -> not a task
        _user(T0 + 60 + 40 * MIN),
    ])
    segs = tokencast.parse_session_segments(str(p), gap_min=30, split_on_user=False)
    assert len(segs) == 1
    assert segs[0]["session"] == "s"  # only one kept -> bare id


# --------------------------------------------------------------------------------------
# Backward compatibility: default path unchanged.
# --------------------------------------------------------------------------------------
def test_parse_session_output_unchanged(tmp_path):
    p = tmp_path / "s.jsonl"
    entries = [_user(T0), _assistant(T0 + 30, out=400), _assistant(T0 + 60, out=600)]
    _write_jsonl(str(p), entries)
    s = tokencast.parse_session(str(p))
    assert s["session"] == "s"
    assert s["assistant_turns"] == 2
    assert s["output"] == 1000
    assert s["files_touched"] == 1  # both edit a.py


def test_load_unchanged_one_session_per_file(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    _write_jsonl(str(d / "x.jsonl"), [_user(T0), _assistant(T0 + 30)])
    _write_jsonl(str(d / "y.jsonl"), [_user(T0), _assistant(T0 + 30)])
    assert len(tokencast.load(str(tmp_path))) == 2


def test_load_segmented_flatmaps_segments(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    _write_jsonl(str(d / "gappy.jsonl"), [
        _user(T0), _assistant(T0 + 30),
        _user(T0 + 60 + 50 * MIN), _assistant(T0 + 60 + 50 * MIN + 30),
    ])
    _write_jsonl(str(d / "single.jsonl"), [_user(T0), _assistant(T0 + 30)])
    segs = tokencast.load_segmented(str(tmp_path), gap_min=30, split_on_user=False)
    assert len(segs) == 3  # 2 from gappy + 1 from single


# --------------------------------------------------------------------------------------
# CLI wiring.
# --------------------------------------------------------------------------------------
def _forecast_args(path, **kw):
    base = dict(path=path, runs=str(path) + "_none", files=None, tools=None, output=None,
                count=None, refresh_prices=False, segment=False, gap_min=30, split_on_user=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _gappy_corpus(d, n):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        _write_jsonl(os.path.join(d, f"s{i}.jsonl"), [
            _user(T0), _assistant(T0 + 30, out=400 + i),
            _user(T0 + 60 + 50 * MIN), _assistant(T0 + 60 + 50 * MIN + 30, out=600 + i),
        ])


def test_cmd_forecast_segment_note_and_more_tasks(tmp_path, capsys):
    d = tmp_path / "hist"
    _gappy_corpus(str(d), 4)  # 4 files -> 8 segments when segmented
    tokencast.cmd_forecast(_forecast_args(str(d), segment=True))
    out = capsys.readouterr().out
    assert "segmented" in out.lower()
    assert "(of 8)" in out  # 8 tasks, not 4 sessions


def test_cmd_forecast_default_unchanged(tmp_path, capsys):
    d = tmp_path / "hist"
    _gappy_corpus(str(d), 6)
    tokencast.cmd_forecast(_forecast_args(str(d), segment=False))
    out = capsys.readouterr().out
    assert "segmented" not in out.lower()
    assert "(of 6)" in out  # 6 sessions, no split


def test_cmd_report_segment_label(tmp_path, capsys):
    d = tmp_path / "hist"
    _gappy_corpus(str(d), 3)
    args = types.SimpleNamespace(path=str(d), cap=None, refresh_prices=False,
                                 segment=True, gap_min=30, split_on_user=False)
    tokencast.cmd_report(args)
    out = capsys.readouterr().out
    assert "Tasks analyzed" in out
    assert "6" in out  # 3 files -> 6 segments
