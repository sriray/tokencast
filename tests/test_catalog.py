import json

from optimize.catalog import available_skills, available_mcp


def test_available_skills_lists_dirs_with_skill_md(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "SKILL.md").write_text("---\ndescription: a\n---\n")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "SKILL.md").write_text("---\ndescription: b\n---\n")
    (tmp_path / "notaskill").mkdir()   # no SKILL.md -> excluded
    assert available_skills(str(tmp_path)) == ["alpha", "beta"]


def test_available_skills_missing_dir_is_empty():
    assert available_skills("/no/such/skills/dir") == []


def test_available_mcp_bare_map(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"pw": {"command": "npx"}, "fs": {"command": "node"}}))
    assert available_mcp(str(p)) == {"pw": {"command": "npx"}, "fs": {"command": "node"}}


def test_available_mcp_wrapper(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"pw": {"command": "npx"}}}))
    assert available_mcp(str(p)) == {"pw": {"command": "npx"}}


def test_available_mcp_missing_or_malformed(tmp_path):
    assert available_mcp("/no/such/file.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json ")
    assert available_mcp(str(bad)) == {}
