# TokenCast `/tokencast-optimize` Front Door — Design Spec (sub-project 6 of 7)

**Date:** 2026-06-05
**Status:** Approved design, pending implementation plan
**Depends on:** sub-projects 1–5 (harness, eval, optimize loop, generator + skills/MCP axes,
task decomposition) and `budget.py` + the light-tier `forecast`. All merged to `main`.

---

## 1. Why this exists

Five sub-projects built the deterministic engine — forecast, eval, the cost-first optimize loop
(model/instructions/tools/skills/MCP axes), budget tracking, and task decomposition — each as a
CLI command. What's missing is the **front door**: a single conversational entry that, given a
project + task + exit criteria, drives the whole machine and tells the engineer what to do. This
sub-project adds (a) a thin deterministic `auto` command that chains the common path in one shot,
and (b) a Claude Code **skill** that wraps it conversationally, using **code for the deterministic
parts and the LLM for judgment** — exactly the split the engineer asked for.

---

## 2. Decisions already made (don't relitigate without reason)

- **Both a skill and a thin `auto` command.** The skill is the conversational playbook; `auto` is
  the deterministic chain it shells out to. The skill expresses judgment (intent, eval-set drafting,
  axis choice, interpretation); `auto`/the CLIs do the math and the runs.
- **`auto` = forecast → optimize → promote, with `--decompose` opt-in.** One confirmation gates all
  spend; one `auto.json` summary. Decomposition is opt-in because it doubles spend.
- **No new optimization logic.** `auto` is glue over `run_optimize` + `run_decompose`; the skill is
  glue over the existing CLIs. Only `optimize/cli.py` gains a command; a small `optimize/auto.py`
  holds the orchestrator.
- **Safety-first.** The skill ALWAYS forecasts and shows the budget before any spend, and never
  bypasses the existing `--yes` confirmation gate.
- **Injectable seams preserved.** `run_auto` threads `runner`/`judge`/`generator`/`decomposer` so the
  whole flow tests zero-spend with no SDK.

---

## 3. Module layout

```
optimize/auto.py                            NEW: run_auto(...) orchestrator
optimize/cli.py                             MODIFY: cmd_auto + the `auto` subparser
.claude/skills/tokencast-optimize/SKILL.md  NEW: the conversational front-door playbook
```

`auto.py` imports `os`, `from optimize.config import AgentConfig`, `from optimize.loop import
run_optimize`, `from optimize.decompose import run_decompose`. Nothing imports the SDK directly
(the leaf seams already gate that). The skill is prose + example command invocations.

---

## 4. `run_auto` orchestrator (`optimize/auto.py`)

```python
run_auto(baseline, evalset, *, runner=None, judge=None, generator=None, decomposer=None,
         candidates=None, n_generated=0, with_decompose=False, min_quality=None, by="cost",
         out_dir="runs", promote_to=None, budget_remaining=None, need_tasks=None) -> dict
```
- Calls `run_optimize(baseline, candidates or [], evalset, runner=runner, judge=judge,
  generator=generator, n_generated=n_generated, min_quality=min_quality, by=by, out_dir=out_dir,
  promote_to=promote_to, budget_remaining=budget_remaining, need_tasks=need_tasks,
  skills_catalog=..., mcp_catalog=...)`. (`run_optimize` already writes `optimize.json` and
  `promoted/`.) Catalogs are passed through if the caller supplies them, else `None`.
- If `with_decompose`: load the promoted winner config from `os.path.join(out_dir, "promoted")`
  (`AgentConfig.load`) and call `run_decompose(evalset, winner_cfg, n=n_generated, runner=runner,
  judge=judge, decomposer=decomposer, min_quality=min_quality, by=by, out_dir=out_dir)`. Else
  `decompose` is `None`.
- Returns `{"winner_id": <optimize result winner>, "optimize": <OptimizeResult>,
  "decompose": <list|None>}` and writes a consolidated `auto.json` (the optimize result summary +
  the decompose results) to `out_dir`.
- `skills_catalog`/`mcp_catalog` params are accepted (default `None`) so `cmd_auto` can resolve and
  pass them; tests omit them.

## 5. `auto` CLI command (`optimize/cli.py`)

`cmd_auto(args)` mirrors `cmd_optimize`:
- Validate `args.evalset` (file) and `args.config` (dir) → `SystemExit` on missing; load `EvalSet`
  + baseline `AgentConfig`.
- Build candidates (`model_sweep(baseline)` when `args.model_sweep`); resolve the budget exactly as
  `cmd_optimize` (the `--budget-remaining`/`--budget-config`/`--budget-scope` block); resolve the
  skills/MCP catalogs via `catalog.available_skills`/`available_mcp` only when `n_generated > 0`.
- Pre-flight: a **combined** estimate — `(1 + len(candidates) + n_generated)` optimize configs
  plus, when `--decompose`, `(1 + n_generated)` decomposition strategies per task — times tasks,
  modeled at the historical p90 (reuse `estimate_cost`/`_fmt_cost`). Print it to stderr.
- The same "real spend" confirmation unless `--yes`.
- Call `run_auto(...)`, then print a unified summary (the optimize ranked winner + cost/quality vs
  baseline, and — if decomposed — the per-task monolithic-vs-decomposition verdicts) and point at
  `auto.json`.
- Flags = the union of `optimize`'s flags (`--config`, `--candidate`, `--model-sweep`, `--repeats`,
  `--min-quality`, `--by`, `--out`, `--promote`, `--history`, `--yes`, `--budget-*`, `--need-tasks`,
  `--generate`, `--skills-dir`, `--mcp-catalog`) plus `--decompose`.

## 6. The skill (`.claude/skills/tokencast-optimize/SKILL.md`)

YAML frontmatter (`name: tokencast-optimize`, a trigger-rich `description`), then a playbook:
- **When to use:** the engineer wants to know what a coding task will cost / how to run it cheaply
  under a budget, on a new or existing project.
- **The deterministic-vs-LLM split (explicit):**
  - *LLM (Claude's judgment):* understand the task/project/exit-criteria; draft or refine the eval
    set (its dimensions ARE the exit criteria; `eval init` can seed one from the repo); pick the
    flags (`--generate N`, `--by`, whether `--decompose` is worth it); interpret the JSON.
  - *Deterministic (shell out; never re-derive the math):* `tokencast.py forecast`,
    `tokencast.py budget`, `tokencast-optimize eval run`, `tokencast-optimize auto`,
    `tokencast-optimize decompose`.
- **Workflow:** intent → eval set (init or author from exit criteria; user reviews it) → FORECAST +
  show budget/runway → confirm → `auto` (optionally `--decompose`) → interpret `auto.json` →
  recommend + the winner is already promoted.
- **Greenfield vs existing codebase:** greenfield → author the eval set from the stated exit
  criteria + a baseline config; existing repo → `eval init` to seed, then refine.
- **Safety:** always forecast and show the budget before spend; surface the confirmation; never
  invent costs (only the provider/accurate-run numbers are authoritative).
- A short **command reference** table.

## 7. Testing (zero spend, no SDK)

- `tests/test_auto.py` — `run_auto` with injected `runner`/`judge`/`generator`/`decomposer`: runs
  optimize and returns a `winner_id`; `with_decompose=True` also returns a non-None `decompose`
  list; writes `auto.json`. A model-sweep candidate set picks the expected winner.
- `tests/test_cli_auto.py` — `cmd_auto` with a monkeypatched `run_auto`: flags pass through
  (`with_decompose`, `n_generated`, `by`, `min_quality`); the combined pre-flight prints; missing
  evalset → `SystemExit`; `--yes` path covered.
- `tests/test_skill_frontdoor.py` — **drift guard**: `.claude/skills/tokencast-optimize/SKILL.md`
  exists; its frontmatter parses with non-empty `name` + `description`; every
  `tokencast-optimize <subcmd>` and `tokencast.py <subcmd>` token it mentions is a real subcommand
  (parsed from the two CLIs' subparsers), so the playbook can't drift from the code.
- Whole suite passes with no `claude-agent-sdk` installed.

## 8. Out of scope (later / never)

- A GUI/web front end → out of scope; the HTML tier stays the light forecaster.
- Auto-running without a confirmation gate → never (spend safety).
- New optimization axes → none; this is purely orchestration.

## 9. Success criteria

- `tokencast-optimize auto evalset.yaml --config DIR --generate N [--decompose]` runs the
  forecast → optimize → promote chain (and decomposition when asked) under one confirmation and
  writes `auto.json`; the winner config is promoted.
- The skill `.claude/skills/tokencast-optimize/SKILL.md` exists, drives the CLIs with the explicit
  deterministic/LLM split, always forecasts + shows budget before spend, and handles both greenfield
  and existing-codebase tasks.
- The drift-guard test passes: the skill references only real subcommands.
- The whole suite runs zero-spend with no SDK; `tokencast.py`/`tokencast.html`/`budget.py` and the
  existing optimize/decompose modules are unchanged (only `cli.py` gains `cmd_auto`, plus the new
  `auto.py` and the skill).
