# TokenCast Task Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tokencast-optimize decompose` command that, per task, compares the monolithic run against N LLM-proposed decompositions (ordered sub-tasks run sequentially in one sandbox, each optionally on a cheaper model) and reports the cheapest strategy meeting a quality floor.

**Architecture:** A new `optimize/decompose.py` defines `SubTask`/`Decomposition`, an injectable `propose_decompositions` (default SDK), `run_decomposed` (multi-step exec in one shared sandbox → a combined `RunResult` → `scorer.score_task`), `compare_task` (monolithic + N decompositions, cost-first under a floor), and `run_decompose` (iterate an evalset). `optimize/cli.py` gets a `decompose` subcommand. Monolithic is just a one-step decomposition. Reuses sandbox/harness/scorer; the optimize loop and light tier are untouched.

**Tech Stack:** Python 3.8+, reuses `AgentConfig`, `harness.run`, `RunResult`, `scorer`, `task_sandbox`, `staging.stage_skills`, `candidates._describe_dimension`, `pricing`. `claude-agent-sdk` only via the lazy default decomposer. `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-task-decomposition-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. A repo security hook blocks the four-letter token e-v-a-l immediately followed by an open paren — never write it.

**Key facts about reused types:**
- `harness.run(task_dict, config, runner=None) -> RunResult`, where `task_dict = {"id","prompt","cwd"}` and `runner(prompt, options, cwd) -> raw dict` with shape `{"turns":[{"model","content"}], "result":{"model_usage":{model:{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}}, "num_turns", "duration_ms", "total_cost_usd", "result_text"}}`.
- `RunResult` fields: `task_id, config_id, model_usage (dict), cost_usd, duration_ms, num_turns, transcript (list), final_output (str), files_changed (list), accurate (bool)`. `cost_usd` is computed by `pricing.cost_from_model_usage`. `to_jsonl` RAISES if `len(model_usage) > 1` (so only write per-step, single-model JSONL — never the combined result).
- `scorer.score_task(task, run_result, cwd, judge=None) -> TaskScore`. Rule dims run checks in `cwd`; judge dims call `judge(prompt)` where the prompt embeds `run_result.final_output`. `TaskScore` fields: `task_id, dimension_scores (dict), composite (float), passed (bool), cost_usd, duration_ms`.
- `optimize/evalset.py`: `EvalTask` has `id`, `prompt`, `dimensions`; `Dimension` has `name`, `weight`, `required`, `checks`, `judge`, `is_rule`.
- `optimize/candidates.py` defines `_describe_dimension(dim)` → judge dim: `f"judge: {dim.judge[:300]}"`; rule dim: `"rule: " + "; ".join(...)` (e.g. `file_exists path=cli.py`).
- `pricing.cost_from_model_usage`: prices via `tokencast.price_for(model)`; bare names `opus`/`sonnet`/`haiku` resolve; output rates haiku $5 / sonnet $15 / opus $25 per 1M.
- `optimize/cli.py`: `main()` builds `sub = ap.add_subparsers(...)`; existing subparsers `run`/`eval`/`optimize` end with `set_defaults`. Helpers `estimate_cost(history_path) -> (n, p90|None)` and `_fmt_cost(x)` exist. cli imports include `from optimize.config import AgentConfig`, `from optimize.evalset import EvalSet`, `import os`, `import sys`.

---

## File Structure

- Create: `optimize/decompose.py` — `SubTask`, `Decomposition`, `propose_decompositions` (+ `_build_decompose_prompt`, `_coerce_plan`, SDK default), `run_decomposed` (+ `_merge_usage`), `compare_task` (+ `_strategy_entry`, `_pct_delta`), `run_decompose`.
- Modify: `optimize/cli.py` — `from optimize.decompose import run_decompose`; `cmd_decompose` + `_print_decompose_results`; the `decompose` subparser.
- Create tests: `tests/test_decompose.py`, `tests/test_cli_decompose.py`. Modify: `tests/test_live_smoke.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.py`, `tokencast.html`, `budget.py`, `optimize/loop.py`, `optimize/candidates.py` (only imported from).

---

### Task 1: `SubTask`/`Decomposition` + `propose_decompositions`

**Files:**
- Create: `optimize/decompose.py`
- Test: `tests/test_decompose.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_decompose.py`:
```python
from optimize.config import AgentConfig
from optimize.decompose import (SubTask, Decomposition, propose_decompositions,
                                 _build_decompose_prompt)
from optimize.evalset import EvalSet


def _task():
    return EvalSet.from_dict({"tasks": [{"id": "slug", "prompt": "Build a CLI",
        "dimensions": [{"name": "tests", "weight": 1, "required": True,
                        "rule": {"kind": "file_exists", "path": "cli.py"}},
                       {"name": "clarity", "judge": "is it clear? 0-1"}]}]}).tasks[0]


def test_propose_validates_and_routes_models():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "step 1", "model": "haiku"},
                           {"prompt": "step 2", "model": "gpt-4"},   # unknown -> None
                           {"prompt": "", "model": "sonnet"},        # empty prompt -> dropped
                           {"model": "opus"}],                        # no prompt -> dropped
                 "note": "split"}]
    plans = propose_decompositions(_task(), "sonnet", n=2, decomposer=fake_dec)
    assert len(plans) == 1
    steps = plans[0].steps
    assert [s.prompt for s in steps] == ["step 1", "step 2"]
    assert steps[0].model == "haiku"
    assert steps[1].model is None          # unknown model -> baseline at run time
    assert plans[0].note == "split"


def test_propose_accepts_bare_list_plan():
    def fake_dec(prompt):
        return [[{"prompt": "only step"}]]
    plans = propose_decompositions(_task(), "sonnet", decomposer=fake_dec)
    assert len(plans) == 1 and plans[0].steps[0].prompt == "only step"


def test_propose_caps_at_n():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "a"}]}, {"steps": [{"prompt": "b"}]},
                {"steps": [{"prompt": "c"}]}]
    plans = propose_decompositions(_task(), "sonnet", n=2, decomposer=fake_dec)
    assert [p.steps[0].prompt for p in plans] == ["a", "b"]


def test_propose_drops_plan_with_no_valid_steps():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": ""}, {"model": "haiku"}]},   # no valid step -> dropped
                {"steps": [{"prompt": "ok"}]}]
    plans = propose_decompositions(_task(), "sonnet", n=5, decomposer=fake_dec)
    assert len(plans) == 1 and plans[0].steps[0].prompt == "ok"


def test_propose_allows_baseline_model_even_if_not_in_default_set():
    def fake_dec(prompt):
        return [{"steps": [{"prompt": "x", "model": "my-custom-model"}]}]
    plans = propose_decompositions(_task(), "my-custom-model", decomposer=fake_dec)
    assert plans[0].steps[0].model == "my-custom-model"   # baseline model always allowed


def test_build_decompose_prompt_includes_task_and_dims():
    prompt = _build_decompose_prompt(_task(), 2)
    assert "Build a CLI" in prompt
    assert "[REQUIRED]" in prompt
    assert "file_exists path=cli.py" in prompt
    assert "is it clear? 0-1" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_decompose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.decompose'`.

- [ ] **Step 3: Write the implementation**

Create `optimize/decompose.py`:
```python
"""Task decomposition: compare a monolithic task run against LLM-proposed decompositions
(ordered sub-tasks, each optionally on a cheaper model), measured accurately. The decomposer
is an injectable seam; the default lazily uses the Agent SDK so everything tests zero-spend."""
import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_decompose.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add optimize/decompose.py tests/test_decompose.py
git commit -m "feat(optimize): SubTask/Decomposition + propose_decompositions (validated, model-routed)"
```

---

### Task 2: `run_decomposed`

**Files:**
- Modify: `optimize/decompose.py`
- Modify: `tests/test_decompose.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_decompose.py`:
```python
import pathlib

from optimize.decompose import run_decomposed


def _usage(out):
    return {"input_tokens": 0, "output_tokens": out,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_run_decomposed_sums_cost_duration_and_merges_models():
    task = _task()
    config = AgentConfig(config_id="b", model="sonnet")
    seq = [
        {"model_usage": {"haiku": _usage(1000)}, "num_turns": 1, "duration_ms": 100,
         "total_cost_usd": 0.0, "result_text": "did step1"},
        {"model_usage": {"sonnet": _usage(1000)}, "num_turns": 2, "duration_ms": 250,
         "total_cost_usd": 0.0, "result_text": "did step2"},
    ]
    calls = {"i": 0}

    def runner(prompt, options, cwd):
        r = seq[calls["i"]]
        calls["i"] += 1
        return {"turns": [{"model": options["model"], "content": []}], "result": r}

    plan = Decomposition([SubTask("s1", "haiku"), SubTask("s2")])   # s2 -> baseline sonnet
    score = run_decomposed(task, config, plan, runner=runner, judge=lambda p: 1.0)
    # haiku 1000 out = $0.005 ; sonnet 1000 out = $0.015 ; sum = $0.02
    assert round(score.cost_usd, 6) == 0.02
    assert score.duration_ms == 350


def test_run_decomposed_shares_cwd_for_final_state_scoring():
    task = _task()   # file_exists cli.py (rule) + clarity (judge)
    config = AgentConfig(config_id="b", model="sonnet")

    def runner(prompt, options, cwd):
        if "create" in prompt:
            pathlib.Path(cwd, "cli.py").write_text("print('hi')\n")
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: _usage(10)}, "num_turns": 1,
                           "duration_ms": 10, "total_cost_usd": 0.0, "result_text": "ok"}}

    plan = Decomposition([SubTask("step1: create cli.py"), SubTask("step2: refine")])
    score = run_decomposed(task, config, plan, runner=runner, judge=lambda p: 1.0)
    assert score.dimension_scores["tests"] == 1.0   # file from step1 survives to the final cwd


def test_run_decomposed_concats_output_for_judge():
    task = EvalSet.from_dict({"tasks": [{"id": "jt", "prompt": "p",
        "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]}).tasks[0]
    config = AgentConfig(config_id="b", model="sonnet")
    seen = {}

    def judge(prompt):
        seen["p"] = prompt
        return 1.0

    def runner(prompt, options, cwd):
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: _usage(1)}, "num_turns": 1,
                           "duration_ms": 1, "total_cost_usd": 0.0,
                           "result_text": "OUT-" + prompt}}

    plan = Decomposition([SubTask("aaa"), SubTask("bbb")])
    run_decomposed(task, config, plan, runner=runner, judge=judge)
    assert "OUT-aaa" in seen["p"] and "OUT-bbb" in seen["p"]   # both steps' output reach the judge
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_decompose.py -k run_decomposed -v`
Expected: FAIL — `ImportError: cannot import name 'run_decomposed'`.

- [ ] **Step 3: Write the implementation**

APPEND to `optimize/decompose.py`:
```python
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
            res = run_task(t, step_config, runner=runner)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_decompose.py -v`
Expected: PASS (the 6 from Task 1 + the 3 new ones).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (5 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/decompose.py tests/test_decompose.py
git commit -m "feat(optimize): run_decomposed (shared-cwd sequential exec + combined scoring)"
```

---

### Task 3: `compare_task` + `run_decompose`

**Files:**
- Modify: `optimize/decompose.py`
- Modify: `tests/test_decompose.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_decompose.py`:
```python
from optimize.decompose import compare_task, run_decompose


def _judge_task():
    return EvalSet.from_dict({"tasks": [{"id": "jt", "prompt": "do it",
        "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]}).tasks[0]


def _flat_runner(prompt, options, cwd):
    # 1000 output tokens per call, regardless of model -> cost scales with the model's rate
    return {"turns": [{"model": options["model"], "content": []}],
            "result": {"model_usage": {options["model"]: _usage(1000)}, "num_turns": 1,
                       "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "done"}}


def test_compare_task_picks_cheaper_decomposition():
    config = AgentConfig(config_id="b", model="sonnet")

    def fake_dec(prompt):
        return [{"steps": [{"prompt": "a", "model": "haiku"},
                           {"prompt": "b", "model": "haiku"}]}]

    res = compare_task(_judge_task(), config, n=1, runner=_flat_runner,
                       judge=lambda p: 1.0, decomposer=fake_dec)
    assert res["winner_label"] == "decomp-1"     # 2x haiku ($0.010) < 1x sonnet ($0.015)
    assert res["cost_delta_pct"] < 0


def test_compare_task_monolithic_wins_when_decomp_below_floor():
    config = AgentConfig(config_id="b", model="sonnet")

    def runner(prompt, options, cwd):
        text = "GOOD" if prompt == "do it" else "BAD"
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: _usage(1000)}, "num_turns": 1,
                           "duration_ms": 100, "total_cost_usd": 0.0, "result_text": text}}

    def fake_dec(prompt):
        return [{"steps": [{"prompt": "a", "model": "haiku"}]}]

    def judge(prompt):
        return 1.0 if "GOOD" in prompt else 0.0

    res = compare_task(_judge_task(), config, n=1, runner=runner, judge=judge,
                       decomposer=fake_dec)
    assert res["winner_label"] == "monolithic"   # decomp quality 0.0 < floor 1.0


def test_compare_task_excludes_zero_cost_strategy():
    config = AgentConfig(config_id="b", model="sonnet")

    def runner(prompt, options, cwd):
        usage = _usage(0) if prompt == "a" else _usage(1000)   # the decomp step costs $0
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {options["model"]: usage}, "num_turns": 1,
                           "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "done"}}

    def fake_dec(prompt):
        return [{"steps": [{"prompt": "a", "model": "haiku"}]}]

    res = compare_task(_judge_task(), config, n=1, runner=runner, judge=lambda p: 1.0,
                       decomposer=fake_dec)
    assert res["winner_label"] == "monolithic"   # $0 decomp excluded despite passing quality


def test_run_decompose_writes_json_and_isolates_failure(tmp_path):
    config = AgentConfig(config_id="b", model="sonnet")
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "do it", "dimensions": [{"name": "q", "judge": "ok 0-1"}]},
        {"id": "t2", "prompt": "do it2", "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]})
    results = run_decompose(evalset, config, n=1, runner=_flat_runner, judge=lambda p: 1.0,
                            decomposer=lambda p: [{"steps": [{"prompt": "x"}]}],
                            out_dir=str(tmp_path / "runs"))
    assert [r["task_id"] for r in results] == ["t1", "t2"]
    assert (tmp_path / "runs" / "decompose.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_decompose.py -k "compare_task or run_decompose" -v`
Expected: FAIL — `ImportError: cannot import name 'compare_task'`.

- [ ] **Step 3: Write the implementation**

APPEND to `optimize/decompose.py`:
```python
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
    with open(os.path.join(out_dir, "decompose.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_decompose.py -v`
Expected: PASS (all decompose tests).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (5 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/decompose.py tests/test_decompose.py
git commit -m "feat(optimize): compare_task + run_decompose (cost-first strategy selection)"
```

---

### Task 4: `decompose` CLI command

**Files:**
- Modify: `optimize/cli.py`
- Test: `tests/test_cli_decompose.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_decompose.py`:
```python
import types

import pytest
import yaml

from optimize import cli


def _evalset_file(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]}))
    return p


def _config_dir(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return d


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                generate=2, min_quality=None, by="cost", out=str(tmp_path / "runs"),
                history=str(tmp_path / "no_history"), yes=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _fake_result():
    return [{"task_id": "t1", "by": "cost", "floor": 1.0,
             "monolithic": {"label": "monolithic", "composite": 1.0, "cost_usd": 0.02,
                            "duration_ms": 100, "passed": True, "steps": [], "note": ""},
             "strategies": [{"label": "monolithic", "cost_usd": 0.02, "duration_ms": 100},
                            {"label": "decomp-1", "cost_usd": 0.01, "duration_ms": 90}],
             "winner_label": "decomp-1", "cost_delta_pct": -50.0, "time_delta_pct": -10.0}]


def test_cmd_decompose_passes_through_and_prints(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_decompose(evalset, config, **k):
        captured.update(k)
        return _fake_result()

    monkeypatch.setattr(cli, "run_decompose", fake_run_decompose)
    cli.cmd_decompose(_args(tmp_path, generate=3, by="time", min_quality=0.7))
    assert captured["n"] == 3
    assert captured["by"] == "time"
    assert captured["min_quality"] == 0.7
    out = capsys.readouterr().out
    assert "t1" in out and "decomp-1" in out


def test_cmd_decompose_missing_evalset(tmp_path):
    with pytest.raises(SystemExit):
        cli.cmd_decompose(_args(tmp_path, evalset=str(tmp_path / "nope.yaml")))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_cli_decompose.py -v`
Expected: FAIL — `AttributeError: module 'optimize.cli' has no attribute 'cmd_decompose'` (and no `run_decompose` attribute to patch).

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py`, add the import near the other optimize imports (after `from optimize.loop import run_optimize`):
```python
from optimize.decompose import run_decompose
```

Add these two functions (place them just before `def main():`):
```python
def _print_decompose_results(results, out_dir):
    for r in results:
        mono = r.get("monolithic")
        if mono is None:
            print(f"{r['task_id']}: (failed)")
            continue
        win = r["winner_label"]
        win_cost = next((s["cost_usd"] for s in r["strategies"] if s["label"] == win),
                        mono["cost_usd"])
        verb = f"decomposition '{win}' wins" if win != "monolithic" else "monolithic wins"
        print(f"{r['task_id']}: {verb} "
              f"(cost {_fmt_cost(mono['cost_usd'])} -> {_fmt_cost(win_cost)}, "
              f"{r['cost_delta_pct']:+.0f}% cost / {r['time_delta_pct']:+.0f}% time)")
    print(f"\nFull results: {os.path.join(out_dir, 'decompose.json')}")


def cmd_decompose(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    baseline = AgentConfig.load(args.config)

    n_gen = max(0, args.generate)
    n_strategies = 1 + n_gen
    n_tasks = len(evalset.tasks)
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * n_strategies
        print(f"Pre-flight: {n_tasks} tasks x {n_strategies} strategies "
              f"(monolithic + up to {n_gen} decompositions); est. total ~ {_fmt_cost(total)} "
              f"(per-task p90, modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)
    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    results = run_decompose(evalset, baseline, n=n_gen, min_quality=args.min_quality,
                            by=args.by, out_dir=args.out)
    _print_decompose_results(results, args.out)
```

In `main()`, register the subparser (after the `optimize` subparser block, before `args = ap.parse_args()` / the dispatch — place it right after `o.set_defaults(func=cmd_optimize)`):
```python
    d = sub.add_parser("decompose",
                       help="compare a monolithic task run vs LLM-proposed decompositions")
    d.add_argument("evalset", help="path to an evalset.yaml")
    d.add_argument("--config", required=True, help="baseline config dir")
    d.add_argument("--generate", type=int, default=2,
                   help="propose N decompositions per task (default 2)")
    d.add_argument("--min-quality", type=float, default=None,
                   help="quality floor (default = the monolithic run's quality)")
    d.add_argument("--by", choices=("cost", "time"), default="cost",
                   help="optimize cost or time")
    d.add_argument("--out", default="./runs", help="output dir (logs + decompose.json)")
    d.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    d.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    d.set_defaults(func=cmd_decompose)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_cli_decompose.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (5 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_decompose.py
git commit -m "feat(optimize): decompose CLI command (monolithic vs decompositions)"
```

---

### Task 5: docs + opt-in live smoke

**Files:**
- Modify: `tests/test_live_smoke.py`, `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Add an opt-in live smoke test**

APPEND to `tests/test_live_smoke.py`:
```python
def test_live_decompose(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK decompose smoke (spends a little)")

    from optimize.config import AgentConfig
    from optimize.evalset import EvalSet
    from optimize.decompose import run_decompose

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    baseline = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [{"name": "file", "weight": 1, "required": True,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    results = run_decompose(evalset, baseline, n=1, out_dir=str(tmp_path / "runs"))
    assert results and results[0]["winner_label"] in (
        s["label"] for s in results[0]["strategies"])
```

- [ ] **Step 2: Verify the suite (new live test skipped by default)**

Run: `python3.11 -m pytest -q`
Expected: PASS; live tests skipped (e.g. "NN passed, 6 skipped").

- [ ] **Step 3: Update `README.md`**

READ `README.md`. In the "Optimizer tier (preview)" section, after the "Optimizing (cost-first, budget-aware)" subsection (and its skills/MCP paragraphs), add a new subsection:
```markdown
### Decomposing a task

Sometimes the cheapest win isn't a different config — it's splitting the task into smaller
sub-tasks (and routing the easy ones to a cheaper model). `decompose` compares the whole-task run
against LLM-proposed decompositions and reports which is actually cheaper at an acceptable quality:

```bash
tokencast-optimize decompose evalset.yaml --config configs/baseline/ --generate 3
```

Each decomposition's sub-tasks run in sequence in one sandbox (later steps see earlier file
changes) and are scored on the same dimensions; per-task it reports the monolithic vs the winning
decomposition's cost/time. Use `--by time` to optimize wall-clock and `--min-quality` to set the
floor (default: the monolithic run's quality).
```

- [ ] **Step 4: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Generator skills/MCP axes (sub-project 4b-ii) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Task decomposition (sub-project 5) — done.** `optimize decompose` compares a monolithic task run
against N LLM-proposed decompositions (sub-tasks run sequentially in one sandbox, each optionally
on a cheaper model) and reports the cheapest strategy meeting a quality floor. The `/tokencast-
optimize` skill front door is sub-project 6.
```

- [ ] **Step 5: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` (generator axes) bullet, add:
```markdown
- `optimize/` (decompose) — `decompose.py` + `tokencast-optimize decompose`: compare a monolithic
  task run vs N LLM-proposed decompositions (ordered sub-tasks run sequentially in one sandbox,
  per-step model routing), scored on the task's dimensions, ranked cost-first under a quality
  floor. Standalone command; injectable decomposer; optimize loop untouched. Sub-project 5.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (live tests skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document task decomposition (sub-project 5); live smoke"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `propose_decompositions` validates plans (drops planless/empty-prompt steps, caps at n) and routes per-step models against the allowed set ∪ baseline model (invalid → baseline); the prompt includes the task's dimension definitions.
- `run_decomposed` runs sub-tasks sequentially in ONE sandbox, sums accurate cost/duration, merges multi-model usage, concatenates output, and scores the FINAL cwd state via the existing scorer (monolithic = one-step).
- `compare_task` selects the cheapest (or fastest) strategy meeting the floor, excluding cost-0 failures, with monolithic always a candidate; `run_decompose` writes `decompose.json` and isolates per-task failures.
- `tokencast-optimize decompose … --generate N` works end to end with a pre-flight estimate + confirmation and a per-task summary.
- The whole suite runs zero-spend with no `claude-agent-sdk` installed; the default SDK decomposer is `# pragma: no cover` + one gated live smoke.
- `tokencast.py`/`tokencast.html`/`budget.py`/`optimize/loop.py` unchanged.
