# TokenCast Task Decomposition — Design Spec (sub-project 5 of 7)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 1 (`AgentConfig`, `harness.run`, `RunResult`), 2 (`run_evalset`,
`task_sandbox`, `scorer.score_task`), 4b-i (`staging.stage_skills`), 4b-ii (dimension-enriched
prompting). All merged to `main`.

---

## 1. Why this exists

Every optimization axis so far mutates the **config** (model, instructions, tools, skills, MCP).
Task **decomposition** is different in kind: it changes how the *task itself is executed* — split a
big prompt into an ordered sequence of smaller sub-tasks, each optionally run on a cheaper model —
without touching the config's other fields. The metering-era payoff: a monolithic task has up to
30x cost variance; decomposing it (and routing easy steps to a cheap model) can be cheaper and more
reliable. TokenCast's ethos is to **measure** that rather than assume it: the `decompose` command
empirically compares the monolithic run against LLM-proposed decompositions and reports which is
actually cheaper/faster at an acceptable quality.

---

## 2. Decisions already made (don't relitigate without reason)

- **Standalone `decompose` command**, not a new axis inside the `optimize` loop and not an EvalTask
  data-model change. It reuses sandbox → harness → scorer but keeps the config-optimize loop
  untouched. Joint config+decomposition optimization is a possible future.
- **Per-sub-task model routing.** A proposed sub-task may name a model (validated against a fixed
  allowed set ∪ the baseline's model; invalid → falls back to the baseline model). This captures
  decomposition's biggest cost lever (route easy steps to haiku).
- **Monolithic = a one-step decomposition.** One `run_decomposed` code path serves both, so the
  comparison is apples-to-apples and there's no separate "whole task" runner.
- **Shared-cwd sequential execution.** Sub-tasks run in order in ONE sandbox cwd; step k sees step
  k-1's file changes. The existing `scorer.score_task` scores the FINAL cwd state (rule checks) +
  the combined output (judge) against the task's existing dimensions.
- **Injectable decomposer seam.** Default lazily uses the Agent SDK; tests inject a fake → zero
  spend, no SDK installed. Same pattern as the 4a/4b-ii generator.
- **Fully optional & isolated.** New module + new subcommand only. `tokencast.py`/`tokencast.html`/
  `budget.py` and the optimize loop are untouched.
- **Budget-runway flags are out of scope for v1.** The cost/time deltas are the value; budget-aware
  decompose can be added later, mirroring the optimize loop.

---

## 3. Module layout

```
optimize/decompose.py  NEW: SubTask, Decomposition; propose_decompositions (+ validation);
                            run_decomposed; compare_task; run_decompose (iterate an evalset)
optimize/cli.py        MODIFY: cmd_decompose + the `decompose` subparser
```

`decompose.py` imports `dataclasses`, `json`, `os` and reuses `optimize.harness.run`,
`optimize.sandbox.task_sandbox`, `optimize.staging.stage_skills`, `optimize.result.RunResult`,
`optimize.scorer`. It never imports the SDK except in the `# pragma: no cover` default decomposer.

---

## 4. The decomposition model + generation (`decompose.py`)

```python
@dataclass
class SubTask:
    prompt: str
    model: Optional[str] = None      # None -> run under the baseline config's model

@dataclass
class Decomposition:
    steps: List[SubTask]
    note: str = ""
```

`propose_decompositions(task, baseline_model, *, n=2, decomposer=None,
allowed_models=("opus", "sonnet", "haiku")) -> List[Decomposition]`:
- Builds a prompt from `task.prompt` AND its dimension definitions (reusing the 4b-ii
  dimension-description style: name, `required` tag, judge rubric / rule summary) so the splitter
  knows what the result is graded on.
- Calls `decomposer(prompt) -> list` (injectable; default = SDK). Each returned plan is a JSON
  object `{"steps": [{"prompt": str, "model": str?}, ...], "note": str?}` (also accept a bare list
  of step objects as a plan).
- **Validation:** a plan must yield ≥1 step with a non-empty string `prompt`; steps with a
  missing/non-string prompt are dropped; a plan with no valid steps is dropped. A step's `model`,
  if present and in `allowed_models ∪ {baseline_model}`, is kept; otherwise set to `None` (→
  baseline model at run time). The LLM never supplies a model outside the fixed set. Output capped
  at `n` plans.

## 5. Execution — `run_decomposed` (`decompose.py`)

`run_decomposed(task, config, decomposition, *, runner=None) -> TaskScore` (well, returns the
scorer's `TaskScore`; see below):
- Opens ONE `task_sandbox(task)` cwd; calls `staging.stage_skills(config, cwd)` once.
- For each `SubTask` in order: `step_config = dataclasses.replace(config, model=step.model or
  config.model)`; `t = {"id": f"{task.id}#s{k}", "prompt": step.prompt, "cwd": cwd}`;
  `step_result = harness.run(t, step_config, runner=runner)`. Optionally write the step's
  single-model JSONL (`f"{task.id}-s{k}-{step_config.config_id}.jsonl"`) — each step is single-model
  so it never trips the `RunResult.to_jsonl` multi-model guard.
- Accumulate across steps into a combined `RunResult` (constructed directly, not `from_raw`):
  - `model_usage`: per-model token dicts summed across steps (naturally multi-model);
  - `cost_usd` = sum of step `cost_usd`; `duration_ms` = sum; `num_turns` = sum;
  - `transcript` = concatenated step transcripts; `final_output` = step `final_output`s joined with
    a blank line; `files_changed` = order-preserving union; `accurate=True`.
- Returns `scorer.score_task(task, combined, cwd, judge=judge)` (so `run_decomposed` also takes a
  `judge=None` kwarg). The combined `TaskScore` carries the summed cost/duration.

## 6. Comparison/selection + CLI (`decompose.py`, `cli.py`)

`compare_task(task, config, *, n=2, runner=None, judge=None, decomposer=None, min_quality=None,
by="cost") -> dict`:
- `monolithic = run_decomposed(task, config, Decomposition([SubTask(task.prompt)]), runner,
  judge)`.
- `plans = propose_decompositions(task, config.model, n=n, decomposer=decomposer)`; each →
  `run_decomposed`.
- `floor = min_quality if min_quality is not None else monolithic.composite`.
- Among the strategies whose `composite >= floor` and `cost_usd > 0` (cost-0 = failed, excluded —
  same rule as the optimize ranker), pick the minimum `cost_usd` (or `duration_ms` when
  `by="time"`). Monolithic is always a candidate. Ties keep the earlier (monolithic-first).
- Returns `{task_id, by, floor, monolithic: {...}, strategies: [{label, composite, cost_usd,
  duration_ms, steps:[{prompt, model}], note}], winner_label, cost_delta_pct, time_delta_pct}`
  where deltas compare the winner to monolithic.

`run_decompose(evalset, config, *, n=2, runner=None, judge=None, decomposer=None, min_quality=None,
by="cost", out_dir="runs") -> list[dict]`: iterate tasks, call `compare_task`, write
`decompose.json` (all per-task results), return them. A per-task failure is isolated (logged,
recorded as a monolithic-only result), mirroring `run_evalset`'s continue-on-failure contract.

`cmd_decompose(args)` + the `decompose` subparser in `main()`:
`tokencast-optimize decompose evalset.yaml --config DIR [--generate N=2] [--min-quality F]
[--by cost|time] [--out ./runs] [--history ...] [--yes]`. Loads the evalset + baseline config,
prints a pre-flight estimate (`(1 + N) × tasks × ` p90, modeled at list prices, same
`estimate_cost`/`_fmt_cost` as optimize) and the same "real spend" confirmation (unless `--yes`),
then calls `run_decompose` and prints a per-task summary: monolithic cost/time/quality vs the
winning decomposition (label, step count, model routing), with the delta and whether decomposition
won.

## 7. Safety / isolation

- Each strategy runs in its own fresh `task_sandbox` (throwaway temp dir / worktree), so strategies
  never contaminate each other and never touch the user's repo.
- The decomposer supplies only sub-prompt **text** and a model **name** from a fixed allowed set;
  an invalid/hallucinated model falls back to the baseline model. No config/tool/command injection.
- The pre-flight + confirmation gate real spend exactly like `optimize`.
- Per-task failure isolation: one bad task can't abort the set.

## 8. Testing (zero spend, no SDK)

- `propose_decompositions`: fake decomposer → valid plans built; a step naming an unknown model →
  that step's model becomes `None` (baseline); steps with non-string/empty prompts dropped; a plan
  with no valid steps dropped; output capped at `n`; the built prompt includes a failing/!required
  dimension's description (dimension enrichment).
- `run_decomposed`: a 2-step plan with per-step models (e.g. haiku then sonnet) under an injected
  runner → combined `cost_usd` = sum of steps, `duration_ms` = sum, `model_usage` merged across both
  models, `final_output` contains both steps' text; a runner that writes a file during step 1 → a
  `file_exists` rule dimension scores 1.0 on the FINAL cwd (proves shared-cwd chaining +
  final-state scoring). Monolithic (one-step) path scored too.
- `compare_task`: a decomposition cheaper than monolithic and meeting the floor → `winner_label` is
  that decomposition with a negative `cost_delta_pct`; a decomposition below the floor → monolithic
  wins; a cost-0 (failed) strategy is excluded.
- `cmd_decompose`: monkeypatched `run_decompose` → the per-task summary prints and `--generate`/
  `--by`/`--min-quality` are passed through; pre-flight + `--yes` path covered.
- One gated (`TOKENCAST_LIVE`) live smoke that decomposes a tiny task end to end.
- Whole suite passes with no `claude-agent-sdk` installed.

## 9. Out of scope (later)

- Budget-runway flags on `decompose` → future, mirroring the optimize loop.
- Joint config + decomposition optimization in one run → future.
- Parallel (non-sequential) sub-task execution / dependency graphs → future; v1 is a linear chain.
- The `/tokencast-optimize` skill front door → sub-project 6.

## 10. Success criteria

- `tokencast-optimize decompose evalset.yaml --config DIR --generate N` runs each task as
  monolithic + N decompositions and reports, per task, the cheapest strategy meeting the quality
  floor with its cost/time delta vs monolithic.
- A decomposition's sub-tasks run sequentially in one sandbox (later steps see earlier file
  changes) and are scored on the task's existing dimensions; per-step model routing works and an
  invalid proposed model falls back to the baseline model.
- The decomposer is injectable: the whole suite runs zero-spend with no SDK; the default SDK
  decomposer is `# pragma: no cover` + one gated live smoke.
- `tokencast.py`/`tokencast.html`/`budget.py` and the optimize loop are unchanged.
