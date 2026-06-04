"""The harness: run one task under one config and return an accurate RunResult.

`run` takes an injectable `runner` so all orchestration is testable with zero API
spend. The default runner is the only code that touches the real Agent SDK.
"""
from optimize.result import RunResult


def run(task, config, runner=None):
    """task: {"id": str, "prompt": str, "cwd": str | None}. config: AgentConfig.
    runner: (prompt, options_dict, cwd) -> raw dict. Defaults to the live SDK runner."""
    runner = runner or _default_runner
    options = config.to_sdk_options()
    raw = runner(task["prompt"], options, task.get("cwd"))
    return RunResult.from_raw(raw, task_id=task["id"], config_id=config.config_id)


def _default_runner(prompt, options, cwd):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_run_sdk(prompt, options, cwd))


async def _run_sdk(prompt, options, cwd):  # pragma: no cover - requires the live SDK
    from claude_agent_sdk import query, ClaudeAgentOptions

    if cwd:
        options = dict(options, cwd=cwd)
    sdk_options = ClaudeAgentOptions(**options)

    turns = []
    result = {}
    async for message in query(prompt=prompt, options=sdk_options):
        mtype = type(message).__name__
        if mtype == "AssistantMessage":
            content = []
            for block in getattr(message, "content", []) or []:
                btype = type(block).__name__
                if btype == "TextBlock":
                    content.append({"type": "text", "text": getattr(block, "text", "")})
                elif btype == "ToolUseBlock":
                    content.append({"type": "tool_use",
                                    "name": getattr(block, "name", ""),
                                    "input": getattr(block, "input", {}) or {}})
            turns.append({"model": getattr(message, "model", options.get("model", "")),
                          "content": content, "timestamp": None})
        elif mtype == "ResultMessage":
            raw_mu = getattr(message, "model_usage", {}) or {}
            norm_mu = {}
            for model, u in raw_mu.items():
                if isinstance(u, dict):
                    norm_mu[model] = u
                else:
                    norm_mu[model] = {
                        "input_tokens": getattr(u, "input_tokens", 0) or 0,
                        "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        "cache_creation_input_tokens":
                            getattr(u, "cache_creation_input_tokens", 0) or 0,
                        "cache_read_input_tokens":
                            getattr(u, "cache_read_input_tokens", 0) or 0,
                    }
            result = {
                "model_usage": norm_mu,
                "num_turns": getattr(message, "num_turns", len(turns)),
                "duration_ms": getattr(message, "duration_ms", 0) or 0,
                "total_cost_usd": getattr(message, "total_cost_usd", 0.0) or 0.0,
                "result_text": getattr(message, "result", "") or "",
            }
    return {"turns": turns, "result": result}
