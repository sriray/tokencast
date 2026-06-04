from optimize.config import AgentConfig
from optimize.harness import run

RAW = {
    "turns": [
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
    ],
    "result": {
        "model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                              "cache_creation_input_tokens": 0,
                                              "cache_read_input_tokens": 10000}},
        "num_turns": 1,
        "duration_ms": 1000,
        "total_cost_usd": 0.0,
        "result_text": "done",
    },
}


def test_run_uses_injected_runner_and_passes_options(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: opus\n")
    config = AgentConfig.load(str(cfg_dir))

    seen = {}

    def fake_runner(prompt, options, cwd):
        seen["prompt"] = prompt
        seen["options"] = options
        seen["cwd"] = cwd
        return RAW

    task = {"id": "task42", "prompt": "do the thing", "cwd": "/tmp/work"}
    result = run(task, config, runner=fake_runner)

    assert seen["prompt"] == "do the thing"
    assert seen["options"]["model"] == "opus"
    assert seen["cwd"] == "/tmp/work"
    assert result.task_id == "task42"
    assert result.config_id == "baseline"
    assert abs(result.cost_usd - 0.0135) < 1e-9
    assert result.files_changed == ["a.py"]
