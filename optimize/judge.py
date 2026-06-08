"""LLM judge behind an injectable seam. Scores a judge dimension in [0, 1].

The default judge lazily uses the Agent SDK (excluded from unit coverage); tests inject a
fake judge so scoring runs with zero spend.
"""


def score_dimension(run_result, dimension, judge=None):
    """judge: callable(prompt: str) -> float. Defaults to the live SDK judge."""
    import math
    judge = judge or _default_judge
    prompt = _build_prompt(run_result, dimension)
    try:
        score = float(judge(prompt))
    except (TypeError, ValueError):
        score = 0.0
    # A NaN/inf score must NOT clamp to a perfect 1.0 (min(1.0, nan) == 1.0), which would
    # satisfy a required-dimension gate and promote a broken config. Treat it as 0.
    if not math.isfinite(score):
        score = 0.0
    return max(0.0, min(1.0, score))


def _build_prompt(run_result, dimension):
    out = run_result.final_output or ""
    return (
        "You are scoring an AI coding agent's run on ONE quality dimension.\n"
        f"Dimension: {dimension.name}\n"
        f"Rubric: {dimension.judge}\n\n"
        f"Agent's final output:\n{out[:4000]}\n\n"
        "Respond with ONLY a single number between 0 and 1."
    )


def _default_judge(prompt):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_judge_sdk(prompt))


async def _judge_sdk(prompt):  # pragma: no cover - requires the live SDK
    import re

    from claude_agent_sdk import query, ClaudeAgentOptions

    text = ""
    async for message in query(prompt=prompt, options=ClaudeAgentOptions(model="haiku")):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    text += getattr(block, "text", "")
    m = re.search(r"[0-9]*\.?[0-9]+", text)
    return float(m.group()) if m else 0.0
