from optimize.evalset import Dimension
from optimize.judge import score_dimension
from optimize.result import RunResult


def _run(final="done"):
    return RunResult(task_id="t", config_id="c", model_usage={}, cost_usd=0.0,
                     duration_ms=0, num_turns=1, transcript=[], final_output=final,
                     files_changed=[], accurate=True)


def _dim():
    return Dimension.from_dict({"name": "clarity", "judge": "Is it clear? 0-1"})


def test_score_uses_injected_judge():
    seen = {}

    def fake_judge(prompt):
        seen["prompt"] = prompt
        return 0.8

    assert score_dimension(_run("hello world"), _dim(), judge=fake_judge) == 0.8
    assert "Is it clear?" in seen["prompt"]
    assert "hello world" in seen["prompt"]


def test_score_is_clamped():
    assert score_dimension(_run(), _dim(), judge=lambda p: 1.5) == 1.0
    assert score_dimension(_run(), _dim(), judge=lambda p: -2) == 0.0
