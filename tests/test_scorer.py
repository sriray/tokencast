import json

from optimize.evalset import EvalTask
from optimize.result import RunResult
from optimize.scorer import EvalReport, TaskScore, score_task


def _run(cost=0.01, dur=1000):
    return RunResult(task_id="t1", config_id="baseline", model_usage={}, cost_usd=cost,
                     duration_ms=dur, num_turns=1, transcript=[], final_output="done",
                     files_changed=[], accurate=True)


def test_score_task_combines_rule_and_judge(tmp_path):
    (tmp_path / "slug.py").write_text("def slugify(x): return x\n")
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p", "pass_threshold": 0.5,
        "dimensions": [
            {"name": "file", "weight": 1, "rule": {"kind": "file_exists", "path": "slug.py"}},
            {"name": "clarity", "weight": 1, "judge": "clear? 0-1"},
        ]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 0.5)
    assert abs(score.composite - 0.75) < 1e-9
    assert score.dimension_scores["file"] == 1.0
    assert score.dimension_scores["clarity"] == 0.5
    assert score.passed is True
    assert score.cost_usd == 0.01


def test_required_rule_gate_fails_task(tmp_path):
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p", "pass_threshold": 0.1,
        "dimensions": [
            {"name": "tests", "weight": 1, "required": True,
             "rule": {"kind": "file_exists", "path": "missing.py"}},
            {"name": "clarity", "weight": 9, "judge": "clear? 0-1"},
        ]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 1.0)
    assert score.dimension_scores["tests"] == 0.0
    assert score.composite >= 0.1
    assert score.passed is False


def test_rule_dimension_fraction(tmp_path):
    (tmp_path / "a.py").write_text("x")
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p",
        "dimensions": [
            {"name": "files", "weight": 1, "rule": [
                {"kind": "file_exists", "path": "a.py"},
                {"kind": "file_exists", "path": "b.py"}]}]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 0.0)
    assert score.dimension_scores["files"] == 0.5


def test_eval_report_aggregates():
    s1 = TaskScore("t1", {"d": 1.0}, composite=0.9, passed=True, cost_usd=0.01, duration_ms=1000)
    s2 = TaskScore("t2", {"d": 0.0}, composite=0.3, passed=False, cost_usd=0.03, duration_ms=3000)
    rep = EvalReport.from_scores("baseline", [s1, s2])
    assert abs(rep.composite - 0.6) < 1e-9
    assert rep.pass_rate == 0.5
    assert abs(rep.total_cost_usd - 0.04) < 1e-9
    assert rep.total_duration_ms == 4000


def test_eval_report_to_json(tmp_path):
    rep = EvalReport.from_scores("baseline", [
        TaskScore("t1", {"d": 1.0}, 1.0, True, 0.01, 1000)])
    p = tmp_path / "report.json"
    rep.to_json(str(p))
    data = json.loads(p.read_text())
    assert data["config_id"] == "baseline"
    assert data["tasks"][0]["task_id"] == "t1"
    assert data["pass_rate"] == 1.0
