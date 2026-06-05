---
name: tokencast-optimize
description: >-
  Estimate and optimize what an agentic coding task will cost and how to run it cheaply
  under a budget. Use when the user asks how much a task will cost, to optimize a config or
  task, to make agentic work cheaper, which model/skills/decomposition is best, or wants a
  pre-flight cost+time forecast before running. Drives TokenCast's forecast, eval, optimize,
  decompose, and budget CLIs.
---

# tokencast-optimize

The conversational front door to TokenCast. Given a coding task (new or existing project) and
its exit criteria, figure out what it will cost, optimize how to run it under a budget, and
recommend a config plus execution strategy.

## Deterministic vs LLM — the split

You (the LLM) own JUDGMENT. The CLIs own the MATH and the runs. Never invent costs or scores;
always shell out for them.

**You decide / draft (LLM):**
- Understand the task, the project, and the exit criteria.
- Draft or refine the eval set — its dimensions ARE the exit criteria. Seed from an existing
  repo, then have the user review it.
- Choose flags: which axes (`--generate N`, `--model-sweep`, `--skills-dir`, `--mcp-catalog`),
  `--by cost` vs `--by time`, and whether `--decompose` is worth the extra spend.
- Interpret the JSON results into a plain recommendation.

**You shell out for (deterministic — never re-derive the math):**
- `tokencast.py forecast` — pre-flight cost/time from history.
- `tokencast.py budget` — remaining budget and runway.
- `tokencast-optimize eval run` — score a config against the eval set.
- `tokencast-optimize auto` — the forecast/optimize/promote chain in one shot.
- `tokencast-optimize decompose` — compare task decompositions.

## Workflow

1. Understand the goal: task, project (greenfield or existing), and exit criteria.
2. Build the eval set. Existing repo: run `tokencast-optimize eval init` to seed it, then
   refine. Greenfield: author dimensions from the stated exit criteria. Show it to the user.
3. Forecast first: run `tokencast.py forecast` and show the p90 cost/time. If a budget is set,
   run `tokencast.py budget` and show the runway. NEVER skip this.
4. Confirm spend: the auto, optimize, and decompose commands gate real spend with a
   confirmation — surface it; do not pass `--yes` unless the user already agreed.
5. Run the chain: `tokencast-optimize auto evalset.yaml --config configs/baseline/ --generate 3`
   (add `--decompose` if splitting the task might help). It optimizes the config, promotes the
   winner, and writes `auto.json`.
6. Interpret and recommend: read `auto.json` — report the winner config, its cost/quality vs
   the baseline, runway gained, and (if decomposed) whether splitting the task wins. The winner
   is already promoted to `<out>/promoted`.

## Greenfield vs existing codebase

- Existing: `tokencast-optimize eval init` reads the repo to draft dimensions; refine with the user.
- Greenfield: no code yet — author the eval set from the exit criteria plus a baseline config
  (model + instructions), then proceed identically.

## Safety

- Always forecast and show the budget before any spend.
- Absolute costs are a FLOOR unless they come from accurate harness runs — say so.
- One confirmation gates all spend in `tokencast-optimize auto`.

## Command reference

| Need | Command |
| --- | --- |
| Pre-flight forecast | `tokencast.py forecast <logs> --files F --tools T` |
| Budget / runway | `tokencast.py budget --config <config.json>` |
| Score one config | `tokencast-optimize eval run evalset.yaml --config DIR` |
| Optimize + promote | `tokencast-optimize auto evalset.yaml --config DIR --generate N` |
| Compare decompositions | `tokencast-optimize decompose evalset.yaml --config DIR` |
