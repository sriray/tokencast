import os

from optimize.candidates import model_sweep
from optimize.config import AgentConfig
from optimize.evalset import EvalSet
from optimize.loop import run_optimize


def _baseline(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(d))


def _evalset():
    return EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok? 0-1"}]}]})


def _runner_cost_by_model(prompt, options, cwd):
    # same tokens for every model -> pricing makes haiku cheapest, opus dearest
    model = options["model"]
    return {"turns": [{"model": model, "content": []}],
            "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                               "cache_creation_input_tokens": 0,
                                               "cache_read_input_tokens": 0}},
                       "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                       "result_text": "done"}}


def test_run_optimize_picks_cheapest_meeting_floor(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)   # baseline-opus, baseline-haiku
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out))
    assert res.winner_id == "baseline-haiku"     # cheapest; all quality 1.0
    assert res.improved is True
    assert (out / "optimize.json").exists()
    assert (out / "promoted" / "metadata.yaml").exists()
    promoted = AgentConfig.load(str(out / "promoted"))
    assert promoted.model == "haiku"


def test_run_optimize_quality_floor_excludes_cheap_but_bad(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)

    def runner(prompt, options, cwd):
        model = options["model"]
        return {"turns": [{"model": model, "content": []}],
                "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                                   "cache_creation_input_tokens": 0,
                                                   "cache_read_input_tokens": 0}},
                           "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                           "result_text": f"ran with {model}"}}

    def judge(prompt):
        return 0.2 if "haiku" in prompt else 1.0   # haiku scores below floor

    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=runner, judge=judge,
                       out_dir=str(out))
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline-haiku"].quality < res.floor   # below baseline's quality floor
    assert res.winner_id != "baseline-haiku"           # so NOT the winner


def test_run_optimize_with_budget_runway(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out), budget_remaining=10.0)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline-haiku"].runway is not None
    assert res.runway_gain is not None and res.runway_gain > 0


def test_run_optimize_all_failed_keeps_baseline(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)

    def failing_runner(prompt, options, cwd):
        raise RuntimeError("boom")

    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=failing_runner,
                       judge=lambda p: 1.0, out_dir=str(out))
    assert res.winner_id == "baseline"   # nothing actually ran -> keep baseline
    assert res.improved is False


def test_run_optimize_uses_generator(tmp_path):
    baseline = _baseline(tmp_path)

    def fake_gen(prompt):
        return [{"system_prompt_append": "fixed instructions"}]

    out = tmp_path / "runs"
    res = run_optimize(baseline, [], _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out),
                       generator=fake_gen, n_generated=1)
    ids = [c.config_id for c in res.candidates]
    assert "baseline" in ids and "baseline-gen1" in ids   # generated candidate was evaluated
    assert (out / "baseline-gen1").exists()                # its run dir was created


def test_run_optimize_no_generator_unchanged(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out))   # no generator
    assert res.winner_id == "baseline-haiku"
    assert all("gen" not in c.config_id for c in res.candidates)


def test_run_optimize_passes_catalogs_to_generator(tmp_path):
    baseline = _baseline(tmp_path)
    seen = {}

    def fake_gen(prompt):
        seen["prompt"] = prompt
        return [{"skills": ["pdf"]}]

    out = tmp_path / "runs"
    run_optimize(baseline, [], _evalset(), runner=_runner_cost_by_model,
                 judge=lambda p: 1.0, out_dir=str(out), generator=fake_gen,
                 n_generated=1, skills_catalog=["pdf"], mcp_catalog={"pw": {}})
    assert "pdf" in seen["prompt"]               # skills catalog reached the prompt
    assert "pw" in seen["prompt"]                # mcp catalog reached the prompt
    assert (out / "baseline-gen1").exists()      # the generated candidate was evaluated
