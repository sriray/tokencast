"""Task decomposition: compare a monolithic task run against LLM-proposed decompositions
(ordered sub-tasks, each optionally on a cheaper model), measured accurately. The decomposer
is an injectable seam; the default lazily uses the Agent SDK so everything tests zero-spend."""
import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from optimize import atomicio
from optimize import scorer as scorer_mod
from optimize import staging
from optimize.candidates import _describe_dimension
from optimize.harness import run as run_task
from optimize.result import RunResult
from optimize.sandbox import task_sandbox

_DEFAULT_MODELS = ("opus", "sonnet", "haiku")


@dataclass
class SubTask:
    prompt: str
    model: Optional[str] = None


@dataclass
class Decomposition:
    steps: List[SubTask] = field(default_factory=list)
    note: str = ""


def _build_decompose_prompt(task, n):
    dim_lines = []
    for dim in task.dimensions:
        tag = "[REQUIRED] " if dim.required else ""
        dim_lines.append(f"- {tag}{dim.name}: {_describe_dimension(dim)}")
    dims = "\n".join(dim_lines) or "- (no dimensions)"
    return (
        "You are decomposing a single coding TASK into an ordered sequence of smaller sub-tasks "
        "that run one after another in the SAME working directory (each sub-task sees the "
        "previous one's changes). The goal is to complete the task more cheaply / reliably; you "
        "may route simple steps to a cheaper model.\n\n"
        f"Task:\n{task.prompt}\n\n"
        f"The final result is graded on these dimensions:\n{dims}\n\n"
        f"Return ONLY a JSON array of up to {n} alternative decompositions. Each item is an "
        "object with \"steps\" (an array of objects, each with a \"prompt\" string and an "
        f"optional \"model\" chosen from {list(_DEFAULT_MODELS)}) and an optional \"note\". "
        "No other keys."
    )


def _coerce_plan(raw, allowed):
    """Validate one raw plan -> Decomposition, or None if it has no valid step."""
    steps_raw = raw.get("steps") if isinstance(raw, dict) else raw
    if not isinstance(steps_raw, list):
        return None
    steps = []
    for s in steps_raw:
        if not isinstance(s, dict):
            continue
        prompt = s.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        model = s.get("model")
        if not (isinstance(model, str) and model in allowed):
            model = None
        steps.append(SubTask(prompt=prompt, model=model))
    if not steps:
        return None
    note = raw.get("note", "") if isinstance(raw, dict) else ""
    return Decomposition(steps=steps, note=note if isinstance(note, str) else "")


def propose_decompositions(task, baseline_model, *, n=2, decomposer=None,
                           allowed_models=_DEFAULT_MODELS):
    """Propose up to n alternative decompositions for `task`. `decomposer` is an injectable
    (prompt: str) -> list; defaults to the live SDK decomposer. A step's model must be in
    allowed_models or equal the baseline model, else it falls back to None (= baseline)."""
    decomposer = decomposer or _default_decomposer
    allowed = set(allowed_models) | {baseline_model}
    prompt = _build_decompose_prompt(task, n)
    raw_plans = decomposer(prompt) or []
    if not isinstance(raw_plans, list):
        raw_plans = []
    out = []
    for raw in raw_plans[:n]:
        plan = _coerce_plan(raw, allowed)
        if plan is not None:
            out.append(plan)
    return out


def _default_decomposer(prompt):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_decompose_sdk(prompt))


async def _decompose_sdk(prompt):  # pragma: no cover - requires the live SDK
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


def _merge_usage(into, model_usage):
    for model, u in (model_usage or {}).items():
        dest = into.setdefault(model, {"input_tokens": 0, "output_tokens": 0,
                                       "cache_creation_input_tokens": 0,
                                       "cache_read_input_tokens": 0})
        for k in dest:
            dest[k] += u.get(k, 0) or 0


def run_decomposed(task, config, decomposition, *, runner=None, judge=None, out_dir=None):
    """Run a decomposition's sub-tasks sequentially in ONE sandbox cwd (each sees the prior
    step's changes), accumulate accurate usage/cost/duration, then score the final state +
    combined output against the task's dimensions. Returns a TaskScore."""
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with task_sandbox(task) as cwd:
        staging.stage_skills(config, cwd)
        combined_usage = {}
        cost = 0.0
        duration = 0
        num_turns = 0
        turns = []
        outputs = []
        files = []
        for k, step in enumerate(decomposition.steps):
            step_config = dataclasses.replace(config, model=step.model or config.model)
            t = {"id": f"{task.id}#s{k}", "prompt": step.prompt, "cwd": cwd}
            try:
                res = run_task(t, step_config, runner=runner)
            except Exception as ex:
                # A sub-task blew up mid-sequence. Stop, but KEEP the cost already incurred
                # by the earlier steps (real API spend) -- scoring the partial state will
                # reflect the incomplete work, and the strategy won't win the quality floor.
                print(f"  ! decomposition step {k} of {task.id} failed: {ex}; "
                      f"scoring partial state", file=sys.stderr)
                break
            if out_dir:
                res.to_jsonl(os.path.join(
                    out_dir, f"{task.id}-s{k}-{step_config.config_id}.jsonl"))
            _merge_usage(combined_usage, res.model_usage)
            cost += res.cost_usd
            duration += res.duration_ms
            num_turns += res.num_turns
            turns.extend(res.transcript)
            if res.final_output:
                outputs.append(res.final_output)
            for fp in res.files_changed:
                if fp not in files:
                    files.append(fp)
        combined = RunResult(
            task_id=task.id, config_id=config.config_id, model_usage=combined_usage,
            cost_usd=cost, duration_ms=duration, num_turns=num_turns, transcript=turns,
            final_output="\n\n".join(outputs), files_changed=files, accurate=True)
        return scorer_mod.score_task(task, combined, cwd, judge=judge)


def _strategy_entry(label, score, decomposition):
    return {"label": label, "composite": score.composite, "cost_usd": score.cost_usd,
            "duration_ms": score.duration_ms, "passed": score.passed,
            "steps": [{"prompt": s.prompt, "model": s.model} for s in decomposition.steps],
            "note": decomposition.note}


def _pct_delta(winner, base):
    return ((winner - base) / base * 100.0) if base else 0.0


def compare_task(task, config, *, n=2, runner=None, judge=None, decomposer=None,
                 min_quality=None, by="cost", out_dir=None):
    """Compare the monolithic run against N proposed decompositions; pick the cheapest (or
    fastest, by="time") whose quality meets the floor (min_quality, default = monolithic's)."""
    def _strategy_out(label):
        return os.path.join(out_dir, task.id, label) if out_dir else None

    mono_decomp = Decomposition([SubTask(task.prompt)], note="monolithic")
    mono_score = run_decomposed(task, config, mono_decomp, runner=runner, judge=judge,
                                out_dir=_strategy_out("monolithic"))
    strategies = [("monolithic", mono_score, mono_decomp)]

    plans = propose_decompositions(task, config.model, n=n, decomposer=decomposer)
    for j, plan in enumerate(plans):
        label = f"decomp-{j + 1}"
        score = run_decomposed(task, config, plan, runner=runner, judge=judge,
                               out_dir=_strategy_out(label))
        strategies.append((label, score, plan))

    floor = min_quality if min_quality is not None else mono_score.composite
    metric = (lambda e: e[1].duration_ms) if by == "time" else (lambda e: e[1].cost_usd)
    eligible = [e for e in strategies if e[1].composite >= floor and e[1].cost_usd > 0]
    winner = min(eligible, key=metric) if eligible else strategies[0]

    return {
        "task_id": task.id, "by": by, "floor": floor,
        "monolithic": _strategy_entry("monolithic", mono_score, mono_decomp),
        "strategies": [_strategy_entry(lbl, sc, dc) for lbl, sc, dc in strategies],
        "winner_label": winner[0],
        "cost_delta_pct": _pct_delta(winner[1].cost_usd, mono_score.cost_usd),
        "time_delta_pct": _pct_delta(winner[1].duration_ms, mono_score.duration_ms),
    }


def run_decompose(evalset, config, *, n=2, runner=None, judge=None, decomposer=None,
                  min_quality=None, by="cost", out_dir="runs"):
    """Run compare_task across an eval set; write decompose.json. One bad task is isolated."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for task in evalset.tasks:
        try:
            results.append(compare_task(
                task, config, n=n, runner=runner, judge=judge, decomposer=decomposer,
                min_quality=min_quality, by=by, out_dir=out_dir))
        except Exception as e:  # isolate: one bad task must not abort the set
            print(f"  ! task {task.id!r} decompose failed: {e}", file=sys.stderr)
            results.append({"task_id": task.id, "by": by, "floor": 0.0, "monolithic": None,
                            "strategies": [], "winner_label": None,
                            "cost_delta_pct": 0.0, "time_delta_pct": 0.0})
    atomicio.dump_json(results, os.path.join(out_dir, "decompose.json"))
    return results
