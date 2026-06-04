import json
import os

from optimize.config import AgentConfig
from optimize.evalset import EvalSet
from optimize.evalrun import run_evalset
from optimize.result import RunResult


def _config(tmp_path):
    cfg = tmp_path / "baseline"
    cfg.mkdir()
    (cfg / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(cfg))


def test_run_evalset_end_to_end(tmp_path):
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "make a.py", "pass_threshold": 0.5,
         "dimensions": [
             {"name": "file", "weight": 1, "rule": {"kind": "file_exists", "path": "a.py"}},
             {"name": "clarity", "weight": 1, "judge": "clear? 0-1"}]}]})

    def fake_runner(prompt, options, cwd):
        with open(os.path.join(cwd, "a.py"), "w") as fh:
            fh.write("print('hi')\n")
        return {
            "turns": [{"model": "claude-sonnet-4-6", "content": []}],
            "result": {"model_usage": {"claude-sonnet-4-6": {
                "input_tokens": 1000, "output_tokens": 500,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 10000}},
                "num_turns": 1, "duration_ms": 2000, "total_cost_usd": 0.0,
                "result_text": "done"},
        }

    out = tmp_path / "runs"
    report = run_evalset(evalset, _config(tmp_path), runner=fake_runner,
                         judge=lambda p: 1.0, out_dir=str(out))

    assert report.config_id == "baseline"
    assert len(report.tasks) == 1
    assert report.tasks[0].dimension_scores["file"] == 1.0
    assert report.tasks[0].passed is True
    assert report.tasks[0].cost_usd > 0
    assert (out / "report.json").exists()
    assert (out / "t1-baseline.jsonl").exists()
    data = json.loads((out / "report.json").read_text())
    assert data["tasks"][0]["task_id"] == "t1"


def test_run_evalset_single_task_set(tmp_path):
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "solo", "prompt": "p",
         "dimensions": [{"name": "c", "judge": "ok? 0-1"}]}]})

    def fake_runner(prompt, options, cwd):
        return {"turns": [], "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}},
            "num_turns": 1, "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "x"}}

    report = run_evalset(evalset, _config(tmp_path), runner=fake_runner,
                         judge=lambda p: 0.7, out_dir=str(tmp_path / "runs"))
    assert len(report.tasks) == 1
    assert abs(report.composite - 0.7) < 1e-9
