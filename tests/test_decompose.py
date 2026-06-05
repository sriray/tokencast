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
