"""AgentConfig: the mutable unit the optimizer will later mutate. Mirrors Agent
Optimizer's .agent_configs/ layout and maps onto SDK option kwargs.

Directory layout:
    config_dir/
      metadata.yaml   # model, budget_usd, max_turns, config_id
      instructions.md # -> system_prompt append
      tools.json      # -> allowed_tools / disallowed_tools / mcp_servers
      skills/         # -> staged skills dir (mapping finalized in a later sub-project)

to_sdk_options() returns a plain kwargs dict (NOT a ClaudeAgentOptions object) so this
module never imports the SDK and stays unit-testable.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class AgentConfig:
    config_id: str
    model: str = "sonnet"
    system_prompt_append: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=list)
    mcp_servers: Dict[str, Any] = field(default_factory=dict)
    skills_source: Optional[str] = None
    skills: Optional[List[str]] = None
    budget_usd: Optional[float] = None
    max_turns: Optional[int] = None

    @classmethod
    def load(cls, path):
        path = os.path.abspath(path)
        default_id = os.path.basename(path.rstrip(os.sep)) or "config"

        meta = {}
        meta_path = os.path.join(path, "metadata.yaml")
        if os.path.exists(meta_path):
            if yaml is None:
                raise RuntimeError(
                    "pyyaml is required to read metadata.yaml; install tokencast[optimize]")
            with open(meta_path, encoding="utf-8") as fh:
                try:
                    meta = yaml.safe_load(fh) or {}
                except yaml.YAMLError as e:
                    raise ValueError(f"failed to parse {meta_path}: {e}")
            if not isinstance(meta, dict):
                raise ValueError(
                    f"{meta_path} must contain a YAML mapping, got {type(meta).__name__}")

        instr = ""
        instr_path = os.path.join(path, "instructions.md")
        if os.path.exists(instr_path):
            with open(instr_path, encoding="utf-8") as fh:
                instr = fh.read().strip()

        tools = {}
        tools_path = os.path.join(path, "tools.json")
        if os.path.exists(tools_path):
            with open(tools_path, encoding="utf-8") as fh:
                try:
                    tools = json.load(fh) or {}
                except json.JSONDecodeError as e:
                    raise ValueError(f"failed to parse {tools_path}: {e}")
            if not isinstance(tools, dict):
                raise ValueError(
                    f"{tools_path} must contain a JSON object, got {type(tools).__name__}")
            for key in ("allowed_tools", "disallowed_tools"):
                if key in tools and not isinstance(tools[key], list):
                    raise ValueError(f"{tools_path}: '{key}' must be a list")
            if "mcp_servers" in tools and not isinstance(tools["mcp_servers"], dict):
                raise ValueError(f"{tools_path}: 'mcp_servers' must be an object")

        skills_dir = os.path.join(path, "skills")
        skills_source = skills_dir if os.path.isdir(skills_dir) else None

        skills = meta.get("skills")
        if skills is not None and not (
                isinstance(skills, list) and all(isinstance(s, str) for s in skills)):
            raise ValueError(f"{meta_path}: 'skills' must be a list of strings")

        return cls(
            config_id=meta.get("config_id", default_id),
            model=meta.get("model", "sonnet"),
            system_prompt_append=instr,
            allowed_tools=tools.get("allowed_tools", []),
            disallowed_tools=tools.get("disallowed_tools", []),
            mcp_servers=tools.get("mcp_servers", {}),
            skills_source=skills_source,
            skills=skills,
            budget_usd=meta.get("budget_usd"),
            max_turns=meta.get("max_turns"),
        )

    def to_sdk_options(self):
        opts = {"model": self.model}
        if self.system_prompt_append:
            opts["system_prompt"] = {"type": "preset", "preset": "claude_code",
                                     "append": self.system_prompt_append}
        if self.allowed_tools:
            opts["allowed_tools"] = list(self.allowed_tools)
        if self.disallowed_tools:
            opts["disallowed_tools"] = list(self.disallowed_tools)
        if self.mcp_servers:
            opts["mcp_servers"] = dict(self.mcp_servers)
        if self.budget_usd is not None:
            opts["max_budget_usd"] = self.budget_usd
        if self.max_turns is not None:
            opts["max_turns"] = self.max_turns
        if self.skills is not None:
            opts["skills"] = list(self.skills)
        # Skills need a discovery source: "project" finds the staged sandbox skills,
        # "user" finds the engineer's ~/.claude/skills. Only set when skills are in play.
        if self.skills is not None or self.skills_source:
            opts["setting_sources"] = ["user", "project"]
        return opts

    def save(self, path):
        """Serialize back to a config dir (round-trips with load). Needs pyyaml."""
        if yaml is None:
            raise RuntimeError("pyyaml is required to save a config; install tokencast[optimize]")
        os.makedirs(path, exist_ok=True)
        meta = {"config_id": self.config_id, "model": self.model}
        if self.budget_usd is not None:
            meta["budget_usd"] = self.budget_usd
        if self.max_turns is not None:
            meta["max_turns"] = self.max_turns
        if self.skills is not None:
            meta["skills"] = list(self.skills)
        with open(os.path.join(path, "metadata.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(meta, fh, sort_keys=False)
        instr_path = os.path.join(path, "instructions.md")
        if self.system_prompt_append:
            with open(instr_path, "w", encoding="utf-8") as fh:
                fh.write(self.system_prompt_append)
        elif os.path.exists(instr_path):
            os.remove(instr_path)
        tools = {}
        if self.allowed_tools:
            tools["allowed_tools"] = list(self.allowed_tools)
        if self.disallowed_tools:
            tools["disallowed_tools"] = list(self.disallowed_tools)
        if self.mcp_servers:
            tools["mcp_servers"] = dict(self.mcp_servers)
        tools_path = os.path.join(path, "tools.json")
        if tools:
            with open(tools_path, "w", encoding="utf-8") as fh:
                json.dump(tools, fh, indent=2)
        elif os.path.exists(tools_path):
            os.remove(tools_path)
        return path
