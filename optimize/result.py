"""RunResult: the accurate, structured output of one harness run.

Authoritative cost/tokens come from the SDK result's per-model usage. RunResult can
also emit Claude Code-schema JSONL so the untouched tokencast.py can report/forecast
on harness output (the bridge between the two tiers).
"""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

import tokencast
from optimize import pricing

_BASE_EPOCH = 1_700_000_000  # fixed baseline so emitted timestamps are deterministic


@dataclass
class RunResult:
    task_id: str
    config_id: str
    model_usage: Dict[str, Dict[str, int]]
    cost_usd: float
    duration_ms: int
    num_turns: int
    transcript: List[Dict[str, Any]]
    final_output: str
    files_changed: List[str] = field(default_factory=list)
    accurate: bool = True

    @classmethod
    def from_raw(cls, raw, task_id, config_id):
        result = raw.get("result", {}) or {}
        turns = raw.get("turns", []) or []
        model_usage = result.get("model_usage", {}) or {}
        files = []
        for turn in turns:
            for block in turn.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("name") in tokencast.FILE_TOOLS:
                        inp = block.get("input") or {}
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp and fp not in files:
                            files.append(fp)
        return cls(
            task_id=task_id,
            config_id=config_id,
            model_usage=model_usage,
            cost_usd=pricing.cost_from_model_usage(model_usage),
            duration_ms=int(result.get("duration_ms", 0) or 0),
            num_turns=int(result.get("num_turns", len(turns)) or len(turns)),
            transcript=turns,
            final_output=result.get("result_text", "") or "",
            files_changed=files,
            accurate=True,
        )
