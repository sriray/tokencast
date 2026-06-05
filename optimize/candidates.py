"""Candidate config sources for the optimize loop. A candidate is an AgentConfig with a
distinct config_id. Sub-project 4's generators produce AgentConfigs the same way."""
import copy
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


def _dedup(seq):
    """Order-preserving de-duplication."""
    return list(dict.fromkeys(seq))


def _describe_dimension(dim):
    """One-line description of what a dimension grades: its judge rubric, or its rule checks."""
    if dim.judge is not None:
        return f"judge: {dim.judge[:300]}"
    parts = []
    for c in dim.checks:
        if c.kind == "command":
            parts.append(f"command: {c.cmd}")
        elif c.kind == "file_exists":
            parts.append(f"file_exists path={c.path}")
        elif c.kind == "file_contains":
            parts.append(f"file_contains path={c.path} pattern={c.pattern}")
        else:
            parts.append(c.kind)
    return "rule: " + "; ".join(parts)


def _format_failing_with_defs(baseline_reports, evalset):
    """Failure lines enriched with each dimension's definition; required dimensions first."""
    defs = {}
    for task in evalset.tasks:
        for dim in task.dimensions:
            defs[(task.id, dim.name)] = dim
    rows = [(t, d, s, defs.get((t, d))) for t, d, s in _failing_dimensions(baseline_reports)]
    rows.sort(key=lambda r: 0 if (r[3] is not None and r[3].required) else 1)
    if not rows:
        return "- (no per-dimension failures recorded)"
    lines = []
    for t, d, s, dim in rows:
        tag = "[REQUIRED] " if (dim is not None and dim.required) else ""
        desc = f" ({_describe_dimension(dim)})" if dim is not None else ""
        lines.append(f"- {tag}task {t!r}: dimension {d!r} scored {s:.2f}{desc}")
    return "\n".join(lines)


def _failing_dimensions(reports):
    """(task_id, dim_name, score) for every dimension that didn't fully pass, across reports."""
    out = []
    for rep in reports:
        for ts in rep.tasks:
            for name, score in ts.dimension_scores.items():
                if score < 1.0:
                    out.append((ts.task_id, name, score))
    return out


def _build_candidate_prompt(baseline, baseline_reports, n, *, evalset=None,
                            skills_catalog=None, mcp_catalog=None):
    if evalset is not None:
        fail_lines = _format_failing_with_defs(baseline_reports, evalset)
    else:
        fails = _failing_dimensions(baseline_reports)
        fail_lines = "\n".join(f"- task {t!r}: dimension {d!r} scored {s:.2f}"
                               for t, d, s in fails) or "- (no per-dimension failures recorded)"
    instr = baseline.system_prompt_append or "(none)"
    skills_line = ", ".join(skills_catalog or []) or "(none available)"
    mcp_line = ", ".join(sorted(mcp_catalog or {})) or "(none available)"
    return (
        "You are improving an AI coding agent's CONFIG to fix its eval failures.\n"
        "You may change the instructions (system prompt append), the tool allow/deny lists, "
        "the skills it may use, and the MCP servers it may use.\n\n"
        f"Current instructions:\n{instr[:3000]}\n\n"
        f"Current allowed_tools: {list(baseline.allowed_tools)}\n"
        f"Current disallowed_tools: {list(baseline.disallowed_tools)}\n\n"
        f"Failing dimensions from the baseline eval:\n{fail_lines}\n\n"
        f"Available skills (choose by name): {skills_line}\n"
        f"Available MCP servers (choose by name): {mcp_line}\n\n"
        f"Return ONLY a JSON array of up to {n} candidate mutations. Each item is an object "
        "with an optional \"system_prompt_append\" (the FULL replacement instructions string), "
        "optional \"allowed_tools\"/\"disallowed_tools\" (arrays), optional \"skills\"/\"mcp\" "
        "(arrays of names chosen ONLY from the lists above), and a short \"note\". No other keys."
    )


def _apply_mutation(baseline, mut, i, *, skills_catalog=None, mcp_catalog=None):
    # Only the supported mutation surface is honored; anything else in `mut` is ignored.
    overrides = {"config_id": f"{baseline.config_id}-gen{i + 1}"}
    if isinstance(mut.get("system_prompt_append"), str):
        overrides["system_prompt_append"] = mut["system_prompt_append"]
    if isinstance(mut.get("allowed_tools"), list):
        overrides["allowed_tools"] = list(mut["allowed_tools"])
    if isinstance(mut.get("disallowed_tools"), list):
        overrides["disallowed_tools"] = list(mut["disallowed_tools"])
    # skills axis: names validated against the catalog, additive-unioned with the baseline's
    if isinstance(mut.get("skills"), list):
        valid = [s for s in mut["skills"]
                 if isinstance(s, str) and s in (skills_catalog or [])]
        if valid:
            overrides["skills"] = _dedup((baseline.skills or []) + valid)
    # mcp axis: names resolved to their catalog configs, additive-merged with the baseline's
    if isinstance(mut.get("mcp"), list):
        resolved = {name: copy.deepcopy((mcp_catalog or {})[name]) for name in mut["mcp"]
                    if isinstance(name, str) and name in (mcp_catalog or {})}
        if resolved:
            overrides["mcp_servers"] = {**baseline.mcp_servers, **resolved}
    # fresh copies of mutable collections not overridden, so variants never alias the baseline
    overrides.setdefault("allowed_tools", list(baseline.allowed_tools))
    overrides.setdefault("disallowed_tools", list(baseline.disallowed_tools))
    overrides.setdefault("mcp_servers", dict(baseline.mcp_servers))
    if "skills" not in overrides and baseline.skills is not None:
        overrides["skills"] = list(baseline.skills)
    return dataclasses.replace(baseline, **overrides)


def generate_candidates(baseline, baseline_reports, *, n=2, generator=None, evalset=None,
                        skills_catalog=None, mcp_catalog=None):
    """Failure-driven: propose up to n candidate AgentConfigs mutating the baseline's
    instructions / tool selection / skills / MCP servers to address its failing dimensions.
    `generator` is an injectable (prompt: str) -> list[dict]; defaults to the live SDK generator.
    skills/MCP names in the output are validated against skills_catalog/mcp_catalog (unknowns
    dropped; MCP names resolved to their catalog configs)."""
    generator = generator or _default_candidate_generator
    prompt = _build_candidate_prompt(baseline, baseline_reports, n, evalset=evalset,
                                     skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)
    mutations = generator(prompt) or []
    if not isinstance(mutations, list):
        mutations = []
    out = []
    for i, mut in enumerate(mutations[:n]):
        if isinstance(mut, dict):
            out.append(_apply_mutation(baseline, mut, i, skills_catalog=skills_catalog,
                                       mcp_catalog=mcp_catalog))
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
