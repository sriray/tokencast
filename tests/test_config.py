import json

import yaml

from optimize.config import AgentConfig


def _write_config(d):
    (d / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "opus", "budget_usd": 2.0, "max_turns": 30}))
    (d / "instructions.md").write_text("Always cite the return policy.\n")
    (d / "tools.json").write_text(json.dumps(
        {"allowed_tools": ["Read", "Edit"], "disallowed_tools": ["Bash"],
         "mcp_servers": {"pw": {"command": "npx"}}}))
    (d / "skills").mkdir()


def test_load_full_config(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    _write_config(cfg_dir)

    cfg = AgentConfig.load(str(cfg_dir))
    assert cfg.config_id == "baseline"
    assert cfg.model == "opus"
    assert cfg.budget_usd == 2.0
    assert cfg.max_turns == 30
    assert cfg.system_prompt_append == "Always cite the return policy."
    assert cfg.allowed_tools == ["Read", "Edit"]
    assert cfg.disallowed_tools == ["Bash"]
    assert cfg.mcp_servers == {"pw": {"command": "npx"}}
    assert cfg.skills_source.endswith("skills")


def test_load_minimal_config_uses_defaults(tmp_path):
    cfg_dir = tmp_path / "min"
    cfg_dir.mkdir()
    cfg = AgentConfig.load(str(cfg_dir))
    assert cfg.config_id == "min"
    assert cfg.model == "sonnet"
    assert cfg.system_prompt_append == ""
    assert cfg.allowed_tools == []
    assert cfg.skills_source is None
    assert cfg.budget_usd is None


def test_to_sdk_options_maps_fields(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    _write_config(cfg_dir)
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()

    assert opts["model"] == "opus"
    assert opts["system_prompt"] == {"type": "preset", "preset": "claude_code",
                                     "append": "Always cite the return policy."}
    assert opts["allowed_tools"] == ["Read", "Edit"]
    assert opts["disallowed_tools"] == ["Bash"]
    assert opts["mcp_servers"] == {"pw": {"command": "npx"}}
    assert opts["max_budget_usd"] == 2.0
    assert opts["max_turns"] == 30
    # 4b-i: a config dir with a skills/ subdir now maps skills_source -> setting_sources.
    assert opts["setting_sources"] == ["user", "project"]
    assert "skills" not in opts   # no `skills` name-list in this fixture's metadata


def test_to_sdk_options_minimal_is_just_model(tmp_path):
    cfg_dir = tmp_path / "min"
    cfg_dir.mkdir()
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert opts == {"model": "sonnet"}


import pytest


def test_load_rejects_non_list_tool_fields(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "tools.json").write_text(json.dumps({"allowed_tools": "Read"}))
    with pytest.raises(ValueError, match="allowed_tools"):
        AgentConfig.load(str(cfg_dir))


def test_load_rejects_non_dict_mcp_servers(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "tools.json").write_text(json.dumps({"mcp_servers": []}))
    with pytest.raises(ValueError, match="mcp_servers"):
        AgentConfig.load(str(cfg_dir))


def test_load_wraps_malformed_json_with_path(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "tools.json").write_text("{ not valid json ")
    with pytest.raises(ValueError, match="tools.json"):
        AgentConfig.load(str(cfg_dir))


def test_load_rejects_non_mapping_metadata(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="metadata.yaml"):
        AgentConfig.load(str(cfg_dir))


def test_to_sdk_options_maps_skills(tmp_path):
    cfg_dir = tmp_path / "withskills"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "sonnet", "skills": ["pdf", "docx"]}))
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert opts["skills"] == ["pdf", "docx"]
    assert opts["setting_sources"] == ["user", "project"]


def test_to_sdk_options_no_skills_no_setting_sources(tmp_path):
    cfg_dir = tmp_path / "plain"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert "skills" not in opts
    assert "setting_sources" not in opts


def test_load_rejects_non_list_skills(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "sonnet", "skills": "pdf"}))
    with pytest.raises(ValueError, match="skills"):
        AgentConfig.load(str(cfg_dir))
