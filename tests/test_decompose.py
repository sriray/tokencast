from optimize.config import AgentConfig
from optimize.decompose import (SubTask, Decomposition, propose_decompositions,
                                 _build_decompose_prompt)
from optimize.evalset import EvalSet


def _task():
    return EvalSet.from_dict({"tasks": [{"id": "slug", "prompt": "Build a CLI",
        "dimensions": [{"name": "tests", "weight": 1, "required": True,
                        "rule": {"kind": "file_exists", "path": "cli.py"}},
                       {"name": "clarity", "judge": "is it clear? 0-1"}]}]}).tasks[0]


def test_propose_validates_and_routes_models():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "step 1", "model": "haiku"},
                           {"prompt": "step 2", "model": "gpt-4"},   # unknown -> None
                           {"prompt": "", "model": "sonnet"},        # empty prompt -> dropped
                           {"model": "opus"}],                        # no prompt -> dropped
                 "note": "split"}]
    plans = propose_decompositions(_task(), "sonnet", n=2, decomposer=fake_dec)
    assert len(plans) == 1
    steps = plans[0].steps
    assert [s.prompt for s in steps] == ["step 1", "step 2"]
    assert steps[0].model == "haiku"
    assert steps[1].model is None          # unknown model -> baseline at run time
    assert plans[0].note == "split"


def test_propose_accepts_bare_list_plan():
    def fake_dec(prompt):
        return [[{"prompt": "only step"}]]
    plans = propose_decompositions(_task(), "sonnet", decomposer=fake_dec)
    assert len(plans) == 1 and plans[0].steps[0].prompt == "only step"


def test_propose_caps_at_n():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "a"}]}, {"steps": [{"prompt": "b"}]},
                {"steps": [{"prompt": "c"}]}]
    plans = propose_decompositions(_task(), "sonnet", n=2, decomposer=fake_dec)
    assert [p.steps[0].prompt for p in plans] == ["a", "b"]


def test_propose_drops_plan_with_no_valid_steps():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": ""}, {"model": "haiku"}]},   # no valid step -> dropped
                {"steps": [{"prompt": "ok"}]}]
    plans = propose_decompositions(_task(), "sonnet", n=5, decomposer=fake_dec)
    assert len(plans) == 1 and plans[0].steps[0].prompt == "ok"


def test_propose_allows_baseline_model_even_if_not_in_default_set():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "x", "model": "my-custom-model"}]}]
    plans = propose_decompositions(_task(), "my-custom-model", decomposer=fake_dec)
    assert plans[0].steps[0].model == "my-custom-model"   # baseline model always allowed


def test_build_decompose_prompt_includes_task_and_dims():
    prompt = _build_decompose_prompt(_task(), 2)
    assert "Build a CLI" in prompt
    assert "[REQUIRED]" in prompt
    assert "file_exists path=cli.py" in prompt
    assert "is it clear? 0-1" in prompt


import pathlib

from optimize.decompose import run_decomposed


def _usage(out):
    return {"input_tokens": 0, "output_tokens": out,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_run_decomposed_sums_cost_duration_and_merges_models():
    task = _task()
    config = AgentConfig(config_id="b", model="sonnet")
    seq = [
        {"model_usage": {"haiku": _usage(1000)}, "num_turns": 1, "duration_ms": 100,
         "total_cost_usd": 0.0, "result_text": "did step1"},
        {"model_usage": {"sonnet": _usage(1000)}, "num_turns": 2, "duration_ms": 250,
         "total_cost_usd": 0.0, "result_text": "did step2"},
    ]
    calls = {"i": 0}

    def runner(prompt, options, cwd):
        r = seq[calls["i"]]
        calls["i"] += 1
        return {"turns": [{"model": options["model"], "content": []}], "result": r}

    plan = Decomposition([SubTask("s1", "haiku"), SubTask("s2")])   # s2 -> baseline sonnet
    score = run_decomposed(task, config, plan, runner=runner, judge=lambda p: 1.0)
    # haiku 1000 out = $0.005 ; sonnet 1000 out = $0.015 ; sum = $0.02
    assert round(score.cost_usd, 6) == 0.02
    assert score.duration_ms == 350


def test_run_decomposed_shares_cwd_for_final_state_scoring():
    task = _task()   # file_exists cli.py (rule) + clarity (judge)
    config = AgentConfig(config_id="b", model="sonnet")

    def runner(prompt, options, cwd):
        if "create" in prompt:
            pathlib.Path(cwd, "cli.py").write_text("print('hi')\n")
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: _usage(10)}, "num_turns": 1,
                           "duration_ms": 10, "total_cost_usd": 0.0, "result_text": "ok"}}

    plan = Decomposition([SubTask("step1: create cli.py"), SubTask("step2: refine")])
    score = run_decomposed(task, config, plan, runner=runner, judge=lambda p: 1.0)
    assert score.dimension_scores["tests"] == 1.0   # file from step1 survives to the final cwd


def test_run_decomposed_concats_output_for_judge():
    task = EvalSet.from_dict({"tasks": [{"id": "jt", "prompt": "p",
        "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]}).tasks[0]
    config = AgentConfig(config_id="b", model="sonnet")
    seen = {}

    def judge(prompt):
        seen["p"] = prompt
        return 1.0

    def runner(prompt, options, cwd):
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: _usage(1)}, "num_turns": 1,
                           "duration_ms": 1, "total_cost_usd": 0.0,
                           "result_text": "OUT-" + prompt}}

    plan = Decomposition([SubTask("aaa"), SubTask("bbb")])
    run_decomposed(task, config, plan, runner=runner, judge=judge)
    assert "OUT-aaa" in seen["p"] and "OUT-bbb" in seen["p"]   # both steps' output reach the judge
