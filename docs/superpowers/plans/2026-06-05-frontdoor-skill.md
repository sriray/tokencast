# TokenCast `/tokencast-optimize` Front Door Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the conversational front door: a deterministic `tokencast-optimize auto` command (forecast → optimize → promote, with `--decompose` opt-in) plus a Claude Code skill that drives the CLIs with an explicit deterministic-vs-LLM split.

**Architecture:** `optimize/auto.py` holds `run_auto`, glue over `run_optimize` (+ optional `run_decompose` on the promoted winner) that writes a combined `auto.json`. `optimize/cli.py` gains `cmd_auto` (mirrors `cmd_optimize` + a `--decompose` flag and a combined pre-flight). `.claude/skills/tokencast-optimize/SKILL.md` is the playbook. No new optimization logic.

**Tech Stack:** Python 3.8+, reuses `run_optimize`, `run_decompose`, `AgentConfig`, `EvalSet`, `model_sweep`, `catalog`, `estimate_cost`/`_fmt_cost`, `_print_optimize_result`/`_print_decompose_results`. `pytest`. The skill is markdown.

**Reference spec:** `docs/superpowers/specs/2026-06-05-frontdoor-skill-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. A repo security hook blocks the four-letter token e-v-a-l immediately followed by an open paren — never write it.

**Key facts about reused types:**
- `run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1, min_quality=None, by="cost", out_dir="runs", promote_to=None, budget_remaining=None, need_tasks=None, generator=None, n_generated=0, skills_catalog=None, mcp_catalog=None) -> OptimizeResult`. It writes `optimize.json` + `promoted/` under `out_dir`.
- `run_decompose(evalset, config, *, n=2, runner=None, judge=None, decomposer=None, min_quality=None, by="cost", out_dir="runs") -> list[dict]` (each dict has `task_id`, `winner_label`, `strategies`, ...). Writes `decompose.json`.
- `OptimizeResult` fields: `baseline_id, floor, winner_id, improved, candidates (list[CandidateResult]), pareto, cost_delta_pct, quality_delta, budget_remaining, need_tasks, runway_gain, winner_fits`. It's a dataclass (`dataclasses.asdict` works); `to_json` exists.
- `CandidateResult` fields incl. `config_id, quality, pass_rate, cost_usd, duration_ms, repeats, cost_min, cost_max, quality_min, quality_max, runway, fits`.
- `optimize/cli.py` already imports: `argparse`, `os`, `sys`, `tokencast`, `from optimize.config import AgentConfig`, `from optimize.harness import run as run_task`, `from optimize.evalset import EvalSet`, `from optimize.evalrun import run_evalset`, `from optimize.generate import ...`, `from optimize.candidates import from_dirs, model_sweep`, `from optimize.loop import run_optimize`, `from optimize import catalog`, `from optimize.decompose import run_decompose`. It has `estimate_cost`, `_fmt_cost`, `cmd_optimize`, `_print_optimize_result`, `cmd_decompose`, `_print_decompose_results`, and in `main()` a `sub = ap.add_subparsers(...)` with subparsers ending `run`/`eval`/`optimize`/`decompose`.
- `model_sweep(baseline)` returns `[baseline-opus, baseline-haiku]` (skips the baseline's own model). With a flat-token runner, `baseline-haiku` is cheapest.

---

## File Structure

- Create: `optimize/auto.py` — `run_auto`.
- Modify: `optimize/cli.py` — `from optimize.auto import run_auto`; `cmd_auto`; the `auto` subparser.
- Create: `.claude/skills/tokencast-optimize/SKILL.md` — the front-door playbook.
- Create tests: `tests/test_auto.py`, `tests/test_cli_auto.py`, `tests/test_skill_frontdoor.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.py`, `tokencast.html`, `budget.py`, `optimize/loop.py`, `optimize/decompose.py`, `optimize/ranking.py`.

---

### Task 1: `run_auto` orchestrator (`optimize/auto.py`)

**Files:**
- Create: `optimize/auto.py`
- Test: `tests/test_auto.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_auto.py`:
```python
import json

from optimize.auto import run_auto
from optimize.candidates import model_sweep
from optimize.config import AgentConfig
from optimize.evalset import EvalSet


def _baseline(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(d))


def _evalset():
    return EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok 0-1"}]}]})


def _runner(prompt, options, cwd):
    model = options["model"]
    return {"turns": [{"model": model, "content": []}],
            "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                               "cache_creation_input_tokens": 0,
                                               "cache_read_input_tokens": 0}},
                       "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                       "result_text": "done"}}


def test_run_auto_optimizes_and_writes_summary(tmp_path):
    baseline = _baseline(tmp_path)
    out = tmp_path / "runs"
    res = run_auto(baseline, _evalset(), runner=_runner, judge=lambda p: 1.0,
                   candidates=model_sweep(baseline), out_dir=str(out))
    assert res["winner_id"] == "baseline-haiku"     # cheapest model wins
    assert res["decompose"] is None
    assert (out / "auto.json").exists()
    data = json.loads((out / "auto.json").read_text())
    assert data["winner_id"] == "baseline-haiku"
    assert data["decompose"] is None
    assert data["optimize"]["winner_id"] == "baseline-haiku"   # serialized OptimizeResult


def test_run_auto_with_decompose(tmp_path):
    baseline = _baseline(tmp_path)
    out = tmp_path / "runs"
    res = run_auto(baseline, _evalset(), runner=_runner, judge=lambda p: 1.0,
                   with_decompose=True, decomposer=lambda p: [{"steps": [{"prompt": "x"}]}],
                   out_dir=str(out))
    assert res["winner_id"] == "baseline"            # no candidates/sweep -> baseline wins
    assert res["decompose"] is not None
    assert res["decompose"][0]["task_id"] == "t1"
    data = json.loads((out / "auto.json").read_text())
    assert data["decompose"][0]["task_id"] == "t1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_auto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.auto'`.

- [ ] **Step 3: Write the implementation**

Create `optimize/auto.py`:
```python
"""run_auto: the deterministic front-door chain. Runs the optimize loop, promotes the winner,
and (optionally) compares task decompositions on that winner, writing a combined auto.json.
Pure glue over run_optimize / run_decompose; the forecast + confirmation live in the CLI."""
import dataclasses
import json
import os

from optimize.config import AgentConfig
from optimize.decompose import run_decompose
from optimize.loop import run_optimize


def run_auto(baseline, evalset, *, runner=None, judge=None, generator=None, decomposer=None,
             candidates=None, repeats=1, n_generated=0, with_decompose=False, min_quality=None,
             by="cost", out_dir="runs", promote_to=None, budget_remaining=None, need_tasks=None,
             skills_catalog=None, mcp_catalog=None):
    """Optimize configs (run_optimize writes optimize.json + promoted/), then — if
    with_decompose — compare task decompositions on the promoted winner. Returns
    {"winner_id", "optimize": OptimizeResult, "decompose": list|None} and writes auto.json."""
    result = run_optimize(
        baseline, candidates or [], evalset, runner=runner, judge=judge, generator=generator,
        repeats=repeats, min_quality=min_quality, by=by, out_dir=out_dir, promote_to=promote_to,
        budget_remaining=budget_remaining, need_tasks=need_tasks, n_generated=n_generated,
        skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)

    decompose = None
    if with_decompose:
        winner_cfg = AgentConfig.load(os.path.join(out_dir, "promoted"))
        decompose = run_decompose(
            evalset, winner_cfg, n=n_generated, runner=runner, judge=judge,
            decomposer=decomposer, min_quality=min_quality, by=by, out_dir=out_dir)

    os.makedirs(out_dir, exist_ok=True)
    summary = {"winner_id": result.winner_id, "optimize": dataclasses.asdict(result),
               "decompose": decompose}
    with open(os.path.join(out_dir, "auto.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return {"winner_id": result.winner_id, "optimize": result, "decompose": decompose}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_auto.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/auto.py tests/test_auto.py
git commit -m "feat(optimize): run_auto orchestrator (optimize -> promote -> optional decompose)"
```

---

### Task 2: `auto` CLI command (`optimize/cli.py`)

**Files:**
- Modify: `optimize/cli.py`
- Test: `tests/test_cli_auto.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_auto.py`:
```python
import types

import pytest
import yaml

from optimize import cli
from optimize.ranking import CandidateResult, OptimizeResult


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


def _cr(cid, quality, cost):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=1.0, cost_usd=cost,
                           duration_ms=1000.0, repeats=1, cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality, runway=None, fits=None)


def _result():
    return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                          improved=False, candidates=[_cr("baseline", 0.9, 0.02)],
                          pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                candidate=None, model_sweep=False, repeats=1, min_quality=None, by="cost",
                out=str(tmp_path / "runs"), promote=None,
                history=str(tmp_path / "no_history"), yes=True,
                budget_remaining=None, budget_config=None, budget_scope="global",
                need_tasks=None, generate=0, skills_dir=None, mcp_catalog=None, decompose=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_auto_passes_through_and_prints(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_auto(baseline, evalset, **k):
        captured.update(k)
        return {"winner_id": "baseline", "optimize": _result(), "decompose": None}

    monkeypatch.setattr(cli, "run_auto", fake_run_auto)
    cli.cmd_auto(_args(tmp_path, generate=2, decompose=True, by="time", min_quality=0.5))
    assert captured["with_decompose"] is True
    assert captured["n_generated"] == 2
    assert captured["by"] == "time"
    assert captured["min_quality"] == 0.5
    out = capsys.readouterr().out
    assert "Winner: baseline" in out
    assert "auto.json" in out


def test_cmd_auto_missing_evalset(tmp_path):
    with pytest.raises(SystemExit):
        cli.cmd_auto(_args(tmp_path, evalset=str(tmp_path / "nope.yaml")))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_cli_auto.py -v`
Expected: FAIL — `AttributeError: module 'optimize.cli' has no attribute 'run_auto'` / `cmd_auto`.

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py`, add the import after `from optimize.decompose import run_decompose`:
```python
from optimize.auto import run_auto
```

Add `cmd_auto` just before `def main():`:
```python
def cmd_auto(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    for d in (args.candidate or []):
        if not os.path.isdir(d):
            raise SystemExit(f"tokencast-optimize: candidate dir not found: {d}")
    if args.need_tasks is not None and args.budget_remaining is None and not args.budget_config:
        raise SystemExit("tokencast-optimize: --need-tasks requires a budget "
                         "(--budget-remaining or --budget-config/--budget-scope)")

    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    baseline = AgentConfig.load(args.config)
    candidates = list(from_dirs(args.candidate or []))
    if args.model_sweep:
        candidates += model_sweep(baseline)

    budget_remaining = args.budget_remaining
    if budget_remaining is None and args.budget_config:
        import budget as budget_mod
        import datetime
        cfg = budget_mod.BudgetConfig.load(args.budget_config)
        if cfg is None:
            raise SystemExit(f"tokencast-optimize: budget config not found: {args.budget_config}")
        try:
            records = budget_mod.collect_spend(
                os.path.expanduser("~/.claude/projects"), "./runs")
            budget_remaining = budget_mod.status(
                cfg, args.budget_scope, records, datetime.date.today()).remaining
        except ValueError as e:
            raise SystemExit(f"tokencast-optimize: {e}")

    n_generated = max(0, args.generate)
    n_cfgs = 1 + len(candidates) + n_generated
    n_tasks = len(evalset.tasks)
    n_decomp = (1 + n_generated) if args.decompose else 0
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * (n_cfgs * args.repeats + n_decomp)
        extra = f" + {n_decomp} decomposition strategies/task" if args.decompose else ""
        print(f"Pre-flight: {n_cfgs} configs x {n_tasks} tasks x {args.repeats} repeats "
              f"(up to {n_generated} generated){extra}; est. total ~ {_fmt_cost(total)} "
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

    skills_catalog = mcp_catalog = None
    if n_generated > 0:
        skills_catalog = catalog.available_skills(args.skills_dir)
        mcp_catalog = catalog.available_mcp(args.mcp_catalog)

    summary = run_auto(baseline, evalset, candidates=candidates, repeats=args.repeats,
                       n_generated=n_generated, with_decompose=args.decompose,
                       min_quality=args.min_quality, by=args.by, out_dir=args.out,
                       promote_to=args.promote, budget_remaining=budget_remaining,
                       need_tasks=args.need_tasks, skills_catalog=skills_catalog,
                       mcp_catalog=mcp_catalog)
    _print_optimize_result(summary["optimize"], args.out)
    if summary["decompose"] is not None:
        _print_decompose_results(summary["decompose"], args.out)
    print(f"Summary:  {os.path.join(args.out, 'auto.json')}")
```

In `main()`, register the `auto` subparser right after the `decompose` block's `d.set_defaults(func=cmd_decompose)`:
```python
    a = sub.add_parser("auto",
                       help="forecast -> optimize -> promote in one shot (+ optional decompose)")
    a.add_argument("evalset", help="path to an evalset.yaml")
    a.add_argument("--config", required=True, help="baseline config dir")
    a.add_argument("--candidate", action="append", help="extra candidate config dir (repeatable)")
    a.add_argument("--model-sweep", action="store_true", help="add opus/sonnet/haiku variants")
    a.add_argument("--repeats", type=int, default=1, help="eval each config N times (median/p90)")
    a.add_argument("--min-quality", type=float, default=None, help="quality floor (default baseline)")
    a.add_argument("--by", choices=("cost", "time"), default="cost", help="optimize cost or time")
    a.add_argument("--out", default="./runs", help="output dir (logs + optimize/auto json + promoted/)")
    a.add_argument("--promote", default=None, help="also save the winner config to this dir")
    a.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    a.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    a.add_argument("--budget-remaining", type=float, default=None,
                   help="remaining budget USD -> show runway gained")
    a.add_argument("--budget-config", default=None,
                   help="resolve remaining from a budget JSON instead of --budget-remaining")
    a.add_argument("--budget-scope", default="global", help="budget scope (global | project:NAME)")
    a.add_argument("--need-tasks", type=int, default=None,
                   help="report whether the winner makes N tasks fit the budget")
    a.add_argument("--generate", type=int, default=0,
                   help="generate N failure-driven candidates (instructions/tools/skills/mcp)")
    a.add_argument("--skills-dir", default=None,
                   help="skills catalog dir for --generate candidates (default ~/.claude/skills)")
    a.add_argument("--mcp-catalog", default=None,
                   help="MCP catalog JSON for --generate candidates (default ~/.claude.json)")
    a.add_argument("--decompose", action="store_true",
                   help="also compare task decompositions on the promoted winner")
    a.set_defaults(func=cmd_auto)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_cli_auto.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_auto.py
git commit -m "feat(optimize): auto CLI command (forecast -> optimize -> promote, +decompose)"
```

---

### Task 3: the skill + drift guard

**Files:**
- Create: `.claude/skills/tokencast-optimize/SKILL.md`
- Test: `tests/test_skill_frontdoor.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_skill_frontdoor.py`:
```python
import re

SKILL = ".claude/skills/tokencast-optimize/SKILL.md"


def _read():
    with open(SKILL, encoding="utf-8") as fh:
        return fh.read()


def _subcommands(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r'add_parser\(\s*"([a-z][a-z-]*)"', src))


def test_skill_exists_with_frontmatter():
    text = _read()
    assert text.startswith("---")
    frontmatter = text.split("---", 2)[1]
    assert re.search(r"^name:\s*\S+", frontmatter, re.M)
    assert re.search(r"^description:\s*\S+", frontmatter, re.M)


def test_skill_references_only_real_subcommands():
    text = _read()
    opt_cmds = _subcommands("optimize/cli.py")
    light_cmds = _subcommands("tokencast.py")
    # match command tokens on the SAME line only (avoid the frontmatter name: line)
    for m in re.finditer(r"tokencast-optimize[ \t]+([a-z][a-z-]*)", text):
        assert m.group(1) in opt_cmds, f"skill references unknown tokencast-optimize subcommand: {m.group(1)!r}"
    for m in re.finditer(r"tokencast\.py[ \t]+([a-z][a-z-]*)", text):
        assert m.group(1) in light_cmds, f"skill references unknown tokencast.py subcommand: {m.group(1)!r}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_skill_frontdoor.py -v`
Expected: FAIL — `FileNotFoundError` (the SKILL.md doesn't exist yet).

- [ ] **Step 3: Write the skill**

Create `.claude/skills/tokencast-optimize/SKILL.md` with EXACTLY this content:
````markdown
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
| Budget / runway | `tokencast.py budget <config.json>` |
| Score one config | `tokencast-optimize eval run evalset.yaml --config DIR` |
| Optimize + promote | `tokencast-optimize auto evalset.yaml --config DIR --generate N` |
| Compare decompositions | `tokencast-optimize decompose evalset.yaml --config DIR` |
````

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_skill_frontdoor.py -v`
Expected: PASS (2 passed). If the drift test fails, a command token in the skill isn't a real subcommand — fix the skill text (every `tokencast-optimize <word>` / `tokencast.py <word>` on a line must be a real subcommand).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/tokencast-optimize/SKILL.md tests/test_skill_frontdoor.py
git commit -m "feat(skill): /tokencast-optimize front-door playbook + drift guard"
```

---

### Task 4: docs

**Files:**
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Update `README.md`**

READ `README.md`. In the "Optimizer tier (preview)" section, immediately after the "### Decomposing a task" subsection (added in sub-project 5), add a new subsection:
```markdown
### One-shot front door

`auto` chains the common path — forecast, optimize (model + instructions + tools + skills + MCP),
promote the winner — behind a single confirmation, and optionally compares task decompositions too:

```bash
tokencast-optimize auto evalset.yaml --config configs/baseline/ --generate 3 --decompose
```

It writes a consolidated `auto.json`. For a conversational driver that drafts the eval set,
forecasts, and interprets the results for you, use the `/tokencast-optimize` skill — it calls these
CLIs for the deterministic work (forecast, scoring, ranking) and handles the judgment (what to
optimize, how to read the numbers) itself.
```

- [ ] **Step 2: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Task decomposition (sub-project 5) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Front door (sub-project 6) — done.** `tokencast-optimize auto` chains forecast → optimize →
promote (+ optional `--decompose`) under one confirmation, and a `/tokencast-optimize` Claude Code
skill drives the whole machine conversationally — code for the deterministic parts (forecast,
scoring, ranking), the LLM for judgment (eval-set drafting, axis choice, interpretation).
```

- [ ] **Step 3: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` (decompose) bullet (added in sub-project 5), add:
```markdown
- `optimize/` (front door) — `auto.py` `run_auto` + `tokencast-optimize auto`: forecast →
  optimize → promote (+ optional `--decompose` on the winner) under one confirmation, writing a
  consolidated `auto.json`. Plus `.claude/skills/tokencast-optimize/SKILL.md`, the conversational
  playbook that drives the CLIs (deterministic math) with LLM judgment (eval drafting, axis choice,
  interpretation). A drift-guard test keeps the skill's command references real. Sub-project 6.
```

- [ ] **Step 4: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 5: Commit**

```bash
git add README.md ROADMAP.md CLAUDE.md
git commit -m "docs: document the /tokencast-optimize front door (sub-project 6)"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `run_auto` runs the optimize loop (promoting the winner), optionally runs decomposition on that winner, and writes `auto.json`; returns the OptimizeResult object + decompose list.
- `tokencast-optimize auto … [--generate N] [--decompose]` works end to end with a combined pre-flight estimate + one confirmation, printing the optimize ranked table (and decomposition verdicts when asked) and pointing at `auto.json`.
- `.claude/skills/tokencast-optimize/SKILL.md` exists with valid frontmatter and the explicit deterministic/LLM split; the drift-guard test confirms it references only real subcommands.
- The whole suite runs zero-spend with no `claude-agent-sdk` installed; `tokencast.py`/`tokencast.html`/`budget.py`/`optimize/loop.py`/`optimize/decompose.py` unchanged (only `cli.py` gains `cmd_auto` + the `auto` subparser, plus the new `auto.py` and the skill).
