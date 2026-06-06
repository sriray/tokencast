from optimize.result import RunResult

RAW = {
    "turns": [
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "text", "text": "hi"},
                     {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}},
                     {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
    ],
    "result": {
        "model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                              "cache_creation_input_tokens": 0,
                                              "cache_read_input_tokens": 10000}},
        "num_turns": 2,
        "duration_ms": 42000,
        "total_cost_usd": 0.013,
        "result_text": "done",
    },
}


def test_from_raw_computes_cost_and_features():
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    assert rr.task_id == "t1"
    assert rr.config_id == "baseline"
    assert abs(rr.cost_usd - 0.0135) < 1e-9          # matches pricing test
    assert rr.duration_ms == 42000
    assert rr.num_turns == 2
    assert rr.final_output == "done"
    assert rr.accurate is True
    # a.py appears twice but is deduped; order preserved
    assert rr.files_changed == ["a.py", "b.py"]


import tokencast


def test_to_jsonl_bridges_to_tokencast(tmp_path):
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    path = tmp_path / "t1-baseline.jsonl"
    rr.to_jsonl(str(path))

    sess = tokencast.parse_session(str(path))
    # The whole point: tokencast's summed cost equals our authoritative cost.
    assert abs(sess["cost"] - rr.cost_usd) < 1e-6
    # Session-level token totals match the authoritative model_usage.
    assert sess["output"] == 500
    assert sess["input"] == 1000
    assert sess["cache_read"] == 10000
    # Features survive: two assistant turns, files deduped, duration in minutes.
    assert sess["assistant_turns"] == 2
    assert sess["files_touched"] == 2
    assert sess["duration_min"] == 0.7  # 42000 ms = 42 s = 0.7 min
    assert sess["accurate"] is True


def test_to_jsonl_handles_zero_turns(tmp_path):
    raw = {"turns": [], "result": dict(RAW["result"])}
    rr = RunResult.from_raw(raw, task_id="t0", config_id="baseline")
    path = tmp_path / "empty.jsonl"
    rr.to_jsonl(str(path))
    sess = tokencast.parse_session(str(path))
    assert abs(sess["cost"] - rr.cost_usd) < 1e-6


import pytest


def test_to_jsonl_rejects_multi_model(tmp_path):
    # The JSONL bridge assumes one model per run; multi-model must fail loudly,
    # not silently misprice. (RunResult.cost_usd stays correct; only to_jsonl guards.)
    raw = {
        "turns": [{"model": "claude-opus-4-8", "content": []}],
        "result": {
            "model_usage": {
                "claude-opus-4-8": {"input_tokens": 1000, "output_tokens": 100,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0},
                "claude-haiku-4-5": {"input_tokens": 1000, "output_tokens": 100,
                                     "cache_creation_input_tokens": 0,
                                     "cache_read_input_tokens": 0},
            },
            "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
            "result_text": "x",
        },
    }
    rr = RunResult.from_raw(raw, task_id="t", config_id="baseline")
    with pytest.raises(ValueError, match="one model per run"):
        rr.to_jsonl(str(tmp_path / "multi.jsonl"))


def test_to_jsonl_multi_turn_settles_on_final_turn(tmp_path):
    # Lock in the mechanic: earlier turns contribute zero, the final turn carries all
    # usage (incl. cache_write), and the bridged cost still equals cost_usd.
    raw = {
        "turns": [
            {"model": "claude-sonnet-4-6",
             "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "p.py"}}]},
            {"model": "claude-sonnet-4-6", "content": [{"type": "text", "text": "mid"}]},
            {"model": "claude-sonnet-4-6", "content": [{"type": "text", "text": "end"}]},
        ],
        "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 2000, "output_tokens": 800,
                                                  "cache_creation_input_tokens": 1500,
                                                  "cache_read_input_tokens": 30000}},
            "num_turns": 3, "duration_ms": 120000, "total_cost_usd": 0.0,
            "result_text": "end",
        },
    }
    rr = RunResult.from_raw(raw, task_id="t3", config_id="baseline")
    path = tmp_path / "multi-turn.jsonl"
    rr.to_jsonl(str(path))
    sess = tokencast.parse_session(str(path))
    assert abs(sess["cost"] - rr.cost_usd) < 1e-6
    assert sess["assistant_turns"] == 3
    assert sess["output"] == 800
    assert sess["cache_write"] == 1500
    assert sess["files_touched"] == 1


def test_to_jsonl_stamps_accuracy_marker(tmp_path):
    import json as _json
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    path = tmp_path / "t1-baseline.jsonl"
    rr.to_jsonl(str(path))
    with open(path, encoding="utf-8") as fh:
        lines = [_json.loads(ln) for ln in fh if ln.strip()]
    assert lines, "expected at least one emitted line"
    assert all(ln.get("tokencast_accurate") is True for ln in lines)
