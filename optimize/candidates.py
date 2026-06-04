"""Candidate config sources for the optimize loop. A candidate is an AgentConfig with a
distinct config_id. Sub-project 4's generators produce AgentConfigs the same way."""
import dataclasses
import json

from optimize.config import AgentConfig

_DEFAULT_MODELS = ("opus", "sonnet", "haiku")


def model_sweep(baseline, models=_DEFAULT_MODELS):
    """One variant per model, skipping the baseline's own model (a redundant re-eval).

    Models are de-duplicated (order-preserving) so config_ids stay unique, and each variant
    gets fresh copies of the mutable collections so it never aliases the baseline's.
    """
    out = []
    for m in dict.fromkeys(models):
        if m == baseline.model:
            continue
        out.append(dataclasses.replace(
            baseline, model=m, config_id=f"{baseline.config_id}-{m}",
            allowed_tools=list(baseline.allowed_tools),
            disallowed_tools=list(baseline.disallowed_tools),
            mcp_servers=dict(baseline.mcp_servers)))
    return out


def from_dirs(paths):
    """Load an AgentConfig from each config dir."""
    return [AgentConfig.load(p) for p in paths]


def _failing_dimensions(reports):
    """(task_id, dim_name, score) for every dimension that didn't fully pass, across reports."""
    out = []
    for rep in reports:
        for ts in rep.tasks:
            for name, score in ts.dimension_scores.items():
                if score < 1.0:
                    out.append((ts.task_id, name, score))
    return out


def _build_candidate_prompt(baseline, baseline_reports, n):
    fails = _failing_dimensions(baseline_reports)
    fail_lines = "\n".join(f"- task {t!r}: dimension {d!r} scored {s:.2f}"
                           for t, d, s in fails) or "- (no per-dimension failures recorded)"
    instr = baseline.system_prompt_append or "(none)"
    return (
        "You are improving an AI coding agent's CONFIG to fix its eval failures.\n"
        "You may change ONLY the instructions (system prompt append) and the tool "
        "allow/deny lists.\n\n"
        f"Current instructions:\n{instr[:3000]}\n\n"
        f"Current allowed_tools: {list(baseline.allowed_tools)}\n"
        f"Current disallowed_tools: {list(baseline.disallowed_tools)}\n\n"
        f"Failing dimensions from the baseline eval:\n{fail_lines}\n\n"
        f"Return ONLY a JSON array of up to {n} candidate mutations. Each item is an object "
        "with an optional \"system_prompt_append\" (the FULL replacement instructions string), "
        "optional \"allowed_tools\"/\"disallowed_tools\" (arrays), and a short \"note\". "
        "No other keys."
    )


def _apply_mutation(baseline, mut, i):
    # Only the 4a mutation surface is honored; anything else in `mut` is ignored.
    overrides = {"config_id": f"{baseline.config_id}-gen{i + 1}"}
    if isinstance(mut.get("system_prompt_append"), str):
        overrides["system_prompt_append"] = mut["system_prompt_append"]
    if isinstance(mut.get("allowed_tools"), list):
        overrides["allowed_tools"] = list(mut["allowed_tools"])
    if isinstance(mut.get("disallowed_tools"), list):
        overrides["disallowed_tools"] = list(mut["disallowed_tools"])
    # fresh copies of mutable collections not overridden, so variants never alias the baseline
    overrides.setdefault("allowed_tools", list(baseline.allowed_tools))
    overrides.setdefault("disallowed_tools", list(baseline.disallowed_tools))
    overrides.setdefault("mcp_servers", dict(baseline.mcp_servers))
    return dataclasses.replace(baseline, **overrides)


def generate_candidates(baseline, baseline_reports, *, n=2, generator=None):
    """Failure-driven: propose up to n candidate AgentConfigs mutating the baseline's
    instructions/tool-selection to address its failing dimensions. `generator` is an injectable
    (prompt: str) -> list[dict]; defaults to the live SDK generator."""
    generator = generator or _default_candidate_generator
    prompt = _build_candidate_prompt(baseline, baseline_reports, n)
    mutations = generator(prompt) or []
    if not isinstance(mutations, list):
        mutations = []
    out = []
    for i, mut in enumerate(mutations[:n]):
        if isinstance(mut, dict):
            out.append(_apply_mutation(baseline, mut, i))
    return out


def _default_candidate_generator(prompt):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_generate_candidates_sdk(prompt))


async def _generate_candidates_sdk(prompt):  # pragma: no cover - requires the live SDK
    import re

    from claude_agent_sdk import query, ClaudeAgentOptions

    text = ""
    async for message in query(prompt=prompt, options=ClaudeAgentOptions(model="sonnet")):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    text += getattr(block, "text", "")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return []
