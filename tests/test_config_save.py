from optimize.config import AgentConfig


def test_save_roundtrips(tmp_path):
    cfg = AgentConfig(
        config_id="baseline-haiku", model="haiku",
        system_prompt_append="Be terse.", allowed_tools=["Read", "Edit"],
        disallowed_tools=["Bash"], mcp_servers={"pw": {"command": "npx"}},
        skills_source=None, budget_usd=2.0, max_turns=30)
    dest = tmp_path / "out"
    cfg.save(str(dest))

    back = AgentConfig.load(str(dest))
    assert back.model == "haiku"
    assert back.system_prompt_append == "Be terse."
    assert back.allowed_tools == ["Read", "Edit"]
    assert back.disallowed_tools == ["Bash"]
    assert back.mcp_servers == {"pw": {"command": "npx"}}
    assert back.budget_usd == 2.0
    assert back.max_turns == 30


def test_save_minimal_omits_empty_files(tmp_path):
    import os
    cfg = AgentConfig(config_id="min", model="sonnet")
    dest = tmp_path / "min"
    cfg.save(str(dest))
    assert os.path.exists(os.path.join(str(dest), "metadata.yaml"))
    assert not os.path.exists(os.path.join(str(dest), "instructions.md"))
    assert not os.path.exists(os.path.join(str(dest), "tools.json"))
    back = AgentConfig.load(str(dest))
    assert back.model == "sonnet"
    assert back.system_prompt_append == ""
    assert back.allowed_tools == []
