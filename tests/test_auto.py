import json

from optimize.auto import run_auto
from optimize.candidates import model_sweep
from optimize.config import AgentConfig
from optimize.evalset import EvalSet


def _baseline(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(d))


def _evalset():
    return EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]})


def _runner(prompt, options, cwd):
    model = options["model"]
    return {"turns": [{"model": model, "content": []}],
            "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                               "cache_creation_input_tokens": 0,
                                               "cache_read_input_tokens": 0}},
                       "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                       "result_text": "done"}}


def test_run_auto_optimizes_and_writes_summary(tmp_path):
    baseline = _baseline(tmp_path)
    out = tmp_path / "runs"
    res = run_auto(baseline, _evalset(), runner=_runner, judge=lambda p: 1.0,
                   candidates=model_sweep(baseline), out_dir=str(out))
    assert res["winner_id"] == "baseline-haiku"     # cheapest model wins
    assert res["decompose"] is None
    assert (out / "auto.json").exists()
    data = json.loads((out / "auto.json").read_text())
    assert data["winner_id"] == "baseline-haiku"
    assert data["decompose"] is None
    assert data["optimize"]["winner_id"] == "baseline-haiku"   # serialized OptimizeResult


def test_run_auto_with_decompose(tmp_path):
    baseline = _baseline(tmp_path)
    out = tmp_path / "runs"
    res = run_auto(baseline, _evalset(), runner=_runner, judge=lambda p: 1.0,
                   with_decompose=True, decomposer=lambda p: [{"steps": [{"prompt": "x"}]}],
                   out_dir=str(out))
    assert res["winner_id"] == "baseline"            # no candidates/sweep -> baseline wins
    assert res["decompose"] is not None
    assert res["decompose"][0]["task_id"] == "t1"
    data = json.loads((out / "auto.json").read_text())
    assert data["decompose"][0]["task_id"] == "t1"
