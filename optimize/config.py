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
            with open(meta_path) as fh:
                meta = yaml.safe_load(fh) or {}

        instr = ""
        instr_path = os.path.join(path, "instructions.md")
        if os.path.exists(instr_path):
            with open(instr_path) as fh:
                instr = fh.read().strip()

        tools = {}
        tools_path = os.path.join(path, "tools.json")
        if os.path.exists(tools_path):
            with open(tools_path) as fh:
                tools = json.load(fh) or {}

        skills_dir = os.path.join(path, "skills")
        skills_source = skills_dir if os.path.isdir(skills_dir) else None

        return cls(
            config_id=meta.get("config_id", default_id),
            model=meta.get("model", "sonnet"),
            system_prompt_append=instr,
            allowed_tools=tools.get("allowed_tools", []),
            disallowed_tools=tools.get("disallowed_tools", []),
            mcp_servers=tools.get("mcp_servers", {}),
            skills_source=skills_source,
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
        # NOTE: skills_source is intentionally NOT mapped to SDK options yet. The exact
        # filesystem-staging mechanism is finalized in the skills-optimization sub-project.
        return opts
