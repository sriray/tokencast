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


def test_run_evalset_isolates_a_failing_task(tmp_path, capsys):
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "good", "prompt": "GOOD",
         "dimensions": [{"name": "c", "judge": "ok? 0-1"}]},
        {"id": "bad", "prompt": "BAD",
         "dimensions": [{"name": "c", "judge": "ok? 0-1"}]}]})

    def fake_runner(prompt, options, cwd):
        if prompt == "BAD":
            raise RuntimeError("boom")
        return {"turns": [], "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}},
            "num_turns": 1, "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "x"}}

    out = tmp_path / "runs"
    report = run_evalset(evalset, _config(tmp_path), runner=fake_runner,
                         judge=lambda p: 0.8, out_dir=str(out))

    by_id = {t.task_id: t for t in report.tasks}
    assert len(report.tasks) == 2                 # both recorded
    assert abs(by_id["good"].composite - 0.8) < 1e-9  # good task scored normally
    assert by_id["good"].passed is True
    assert by_id["bad"].composite == 0.0          # failed task -> zero score
    assert by_id["bad"].passed is False
    assert (out / "report.json").exists()         # report still written despite the failure
    assert "bad" in capsys.readouterr().err       # failure logged to stderr


def test_run_evalset_stages_skills(tmp_path):
    cfg_dir = tmp_path / "baseline"
    (cfg_dir / "skills" / "myskill").mkdir(parents=True)
    (cfg_dir / "skills" / "myskill" / "SKILL.md").write_text("---\ndescription: x\n---\n")
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    config = AgentConfig.load(str(cfg_dir))

    seen = {}

    def fake_runner(prompt, options, cwd):
        seen["staged"] = os.path.isfile(
            os.path.join(cwd, ".claude", "skills", "myskill", "SKILL.md"))
        return {"turns": [], "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}},
            "num_turns": 1, "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "x"}}

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "t", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]})
    run_evalset(evalset, config, runner=fake_runner, judge=lambda p: 1.0,
                out_dir=str(tmp_path / "runs"))
    assert seen["staged"] is True   # the staged skill was present in the run's cwd
