from optimize.candidates import generate_candidates, _build_candidate_prompt, _apply_mutation
from optimize.config import AgentConfig
from optimize.evalset import EvalSet
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


def test_apply_mutation_skills_additive_validated():
    baseline = AgentConfig(config_id="b", model="sonnet", skills=["base"])
    cand = _apply_mutation(baseline, {"skills": ["pdf", "nope", "base"]}, 0,
                           skills_catalog=["pdf", "base"], mcp_catalog={})
    assert cand.skills == ["base", "pdf"]      # union, deduped, unknown 'nope' dropped
    assert baseline.skills == ["base"]          # no aliasing


def test_apply_mutation_skills_promotes_none_baseline():
    baseline = AgentConfig(config_id="b", model="sonnet")   # skills None
    cand = _apply_mutation(baseline, {"skills": ["pdf"]}, 0,
                           skills_catalog=["pdf"], mcp_catalog={})
    assert cand.skills == ["pdf"]


def test_apply_mutation_mcp_resolved_from_catalog():
    baseline = AgentConfig(config_id="b", model="sonnet",
                           mcp_servers={"keep": {"command": "x"}})
    cat = {"playwright": {"command": "npx", "args": ["pw"]}}
    cand = _apply_mutation(baseline, {"mcp": ["playwright", "ghost"]}, 0,
                           skills_catalog=[], mcp_catalog=cat)
    assert cand.mcp_servers == {"keep": {"command": "x"},
                                "playwright": {"command": "npx", "args": ["pw"]}}
    assert "ghost" not in cand.mcp_servers       # unknown dropped


def test_apply_mutation_empty_catalog_drops_axes():
    baseline = AgentConfig(config_id="b", model="sonnet")
    cand = _apply_mutation(baseline, {"skills": ["pdf"], "mcp": ["x"],
                                      "system_prompt_append": "z"}, 0)
    assert cand.skills is None                   # nothing valid -> untouched
    assert cand.mcp_servers == {}
    assert cand.system_prompt_append == "z"       # other axes still apply


def test_build_prompt_required_first_with_defs_and_catalogs():
    baseline = AgentConfig(config_id="b", model="sonnet")
    evalset = EvalSet.from_dict({"tasks": [{"id": "slug", "prompt": "p", "dimensions": [
        {"name": "clarity", "judge": "is it clear? 0-1"},
        {"name": "tests", "weight": 1, "required": True,
         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})
    report = EvalReport(config_id="b", tasks=[TaskScore(
        task_id="slug", dimension_scores={"clarity": 0.5, "tests": 0.0},
        composite=0.25, passed=False, cost_usd=0.01, duration_ms=10)],
        composite=0.25, pass_rate=0.0, total_cost_usd=0.01, total_duration_ms=10)
    prompt = _build_candidate_prompt(baseline, [report], 2, evalset=evalset,
                                     skills_catalog=["pdf", "docx"],
                                     mcp_catalog={"playwright": {}})
    assert prompt.index("tests") < prompt.index("clarity")   # required dim listed first
    assert "[REQUIRED]" in prompt
    assert "file_exists path=pong.txt" in prompt             # rule summary
    assert "is it clear? 0-1" in prompt                      # judge rubric
    assert "pdf, docx" in prompt
    assert "playwright" in prompt


def test_build_prompt_empty_catalogs_say_none():
    baseline = AgentConfig(config_id="b", model="sonnet")
    prompt = _build_candidate_prompt(baseline, [_report_with_failure()], 1)
    assert "(none available)" in prompt


def test_generate_candidates_skills_mcp_end_to_end():
    baseline = AgentConfig(config_id="b", model="sonnet")

    def fake_gen(prompt):
        return [{"skills": ["pdf"], "mcp": ["playwright"], "note": "x"}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=1, generator=fake_gen,
                                skills_catalog=["pdf"],
                                mcp_catalog={"playwright": {"command": "npx"}})
    assert cands[0].skills == ["pdf"]
    assert cands[0].mcp_servers == {"playwright": {"command": "npx"}}
