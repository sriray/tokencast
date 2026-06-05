"""Catalogs of available skills + MCP servers for the failure-driven generator (sub-project
4b-ii). Pure stdlib; never raises on missing/malformed input (degrades to empty)."""
import json
import os


def available_skills(skills_dir=None):
    """Sorted names of skill dirs (those containing a SKILL.md). Defaults to ~/.claude/skills."""
    skills_dir = skills_dir or os.path.expanduser("~/.claude/skills")
    if not os.path.isdir(skills_dir):
        return []
    return sorted(
        name for name in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md")))


def available_mcp(catalog_path=None):
    """{server_name: server_config}. With an explicit catalog_path, accepts a bare {name: cfg}
    map or a {"mcpServers": {...}} wrapper. With no path, reads ~/.claude.json's mcpServers.
    Missing/malformed -> {}."""
    path = catalog_path or os.path.expanduser("~/.claude.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    if catalog_path:
        return data   # an explicit catalog file may be a bare {name: cfg} map
    return {}
