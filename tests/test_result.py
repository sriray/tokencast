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
