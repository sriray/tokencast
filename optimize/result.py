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

    def _settlement_usage(self):
        """Sum authoritative per-model usage into one totals dict (single model per run)."""
        totals = {"input_tokens": 0, "output_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        for u in self.model_usage.values():
            for k in totals:
                totals[k] += u.get(k, 0) or 0
        return totals

    def _settlement_model(self):
        return next(iter(self.model_usage), "unknown")

    def to_jsonl(self, path):
        """Write Claude Code-schema JSONL. The entire authoritative usage is attached to
        the FINAL assistant turn, so tokencast.py's per-turn sums equal the accurate
        totals exactly. Earlier turns carry content (for feature extraction) but zero usage.
        """
        import datetime

        def iso(epoch):
            return datetime.datetime.utcfromtimestamp(epoch).isoformat() + "Z"

        zero = {"input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        settle = self._settlement_usage()
        model = self._settlement_model()
        t0 = _BASE_EPOCH
        t1 = _BASE_EPOCH + max(0, self.duration_ms) / 1000.0

        lines = [{"type": "user", "timestamp": iso(t0),
                  "message": {"role": "user", "content": "task"}}]
        turns = self.transcript or [{"model": model, "content": []}]
        n = len(turns)
        for i, turn in enumerate(turns):
            is_last = (i == n - 1)
            lines.append({"type": "assistant", "timestamp": iso(t1 if is_last else t0),
                          "message": {"role": "assistant",
                                      "model": turn.get("model", model),
                                      "content": turn.get("content", []),
                                      "usage": dict(settle) if is_last else dict(zero)}})
        with open(path, "w") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
        return path
