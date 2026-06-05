from optimize.candidates import generate_candidates, _build_candidate_prompt
from optimize.config import AgentConfig
from optimize.scorer import EvalReport, TaskScore


def _report_with_failure():
    ts = TaskScore(task_id="slug", dimension_scores={"tests": 0.0, "clarity": 0.5},
                   composite=0.3, passed=False, cost_usd=0.01, duration_ms=1000)
    return EvalReport(config_id="baseline", tasks=[ts], composite=0.3, pass_rate=0.0,
                      total_cost_usd=0.01, total_duration_ms=1000)


def test_generate_candidates_applies_mutations_in_surface():
    baseline = AgentConfig(config_id="baseline", model="sonnet",
                           system_prompt_append="old", allowed_tools=["Read"])

    def fake_gen(prompt):
        return [{"system_prompt_append": "new better instructions",
                 "allowed_tools": ["Read", "Edit"], "note": "x"},
                {"disallowed_tools": ["Bash"]}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=2, generator=fake_gen)
    assert [c.config_id for c in cands] == ["baseline-gen1", "baseline-gen2"]
    assert cands[0].system_prompt_append == "new better instructions"
    assert cands[0].allowed_tools == ["Read", "Edit"]
    assert cands[1].system_prompt_append == "old"
    assert cands[1].disallowed_tools == ["Bash"]


def test_generate_candidates_ignores_out_of_surface_fields():
    baseline = AgentConfig(config_id="b", model="sonnet")

    def fake_gen(prompt):
        return [{"model": "opus", "budget_usd": 99, "system_prompt_append": "z"}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=1, generator=fake_gen)
    assert cands[0].model == "sonnet"
    assert cands[0].budget_usd is None
    assert cands[0].system_prompt_append == "z"


def test_generate_candidates_fresh_collections():
    baseline = AgentConfig(config_id="b", model="sonnet", allowed_tools=["Read"])
    cands = generate_candidates(baseline, [_report_with_failure()], n=1,
                                generator=lambda p: [{"note": "noop"}])
    cands[0].allowed_tools.append("Write")
    assert baseline.allowed_tools == ["Read"]


def test_generate_candidates_empty_and_cap():
    baseline = AgentConfig(config_id="b", model="sonnet")
    assert generate_candidates(baseline, [_report_with_failure()], n=2,
                               generator=lambda p: []) == []
    three = generate_candidates(baseline, [_report_with_failure()], n=2,
                                generator=lambda p: [{"note": "1"}, {"note": "2"}, {"note": "3"}])
    assert len(three) == 2


def test_generate_candidates_non_list_return_is_empty():
    baseline = AgentConfig(config_id="b", model="sonnet")
    # a misbehaving generator returns a dict instead of a list -> treat as no candidates
    assert generate_candidates(baseline, [_report_with_failure()], n=2,
                               generator=lambda p: {"oops": 1}) == []


def test_build_candidate_prompt_includes_failures_and_instructions():
    baseline = AgentConfig(config_id="b", model="sonnet", system_prompt_append="BE TERSE")
    prompt = _build_candidate_prompt(baseline, [_report_with_failure()], 2)
    assert "BE TERSE" in prompt
    assert "tests" in prompt and "slug" in prompt
