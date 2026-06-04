# TokenCast Failure-Driven Candidate Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM generator that reads the baseline's eval failures and proposes targeted candidate configs (instructions + tool-selection axes), integrated into the optimize loop as a baseline-first generate-then-evaluate-then-rank cycle.

**Architecture:** `generate_candidates` lives in `optimize/candidates.py` behind an injectable seam (default lazily uses the Agent SDK; tests inject a fake → zero spend). `run_optimize` is reordered to evaluate the baseline first, generate from its failures, then evaluate the rest. The generator may only touch `system_prompt_append`/`allowed_tools`/`disallowed_tools` (the 4a mutation surface). Fully optional: `--generate 0` (default) = identical to 3b.

**Tech Stack:** Python 3.8+, reuses `AgentConfig`, `EvalReport`/`TaskScore`, `run_evalset`, `ranking`, `run_optimize`. `claude-agent-sdk` only via the lazy default generator. `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-failure-driven-generator-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

**Key facts about reused types:**
- `EvalReport` fields: `config_id, tasks (list[TaskScore]), composite, pass_rate, total_cost_usd, total_duration_ms`.
- `TaskScore` fields: `task_id, dimension_scores (dict[str,float]), composite, passed, cost_usd, duration_ms`.
- `AgentConfig` fields incl. `config_id, model, system_prompt_append, allowed_tools, disallowed_tools, mcp_servers`.
- `optimize/candidates.py` currently imports `dataclasses` + `from optimize.config import AgentConfig`, and defines `model_sweep`/`from_dirs`.
- `optimize/loop.py` defines `run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1, min_quality=None, by="cost", out_dir="runs", promote_to=None, budget_remaining=None, need_tasks=None)`.
- `optimize/cli.py` has `cmd_optimize`, `_print_optimize_result`, the `optimize` subparser, `estimate_cost`, `_fmt_cost`.

---

## File Structure

- Modify: `optimize/candidates.py` — add `generate_candidates`, `_failing_dimensions`, `_build_candidate_prompt`, `_apply_mutation`, `_default_candidate_generator`/`_generate_candidates_sdk`.
- Modify: `optimize/loop.py` — reorder `run_optimize`; add `generator=`, `n_generated=`.
- Modify: `optimize/cli.py` — `--generate N` flag, pre-flight count, pass-through.
- Create test: `tests/test_generate_candidates.py`. Modify: `tests/test_loop.py`, `tests/test_cli_optimize.py`, `tests/test_live_smoke.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

---

### Task 1: `generate_candidates` + helpers

**Files:**
- Modify: `optimize/candidates.py`
- Test: `tests/test_generate_candidates.py`

- [ ] **Step 1: Write the failing test**

`tests/test_generate_candidates.py`:
```python
from optimize.candidates import generate_candidates, _build_candidate_prompt
from optimize.config import AgentConfig
from optimize.scorer import EvalReport, TaskScore


def _report_with_failure():
    ts = TaskScore(task_id="slug", dimension_scores={"tests": 0.0, "clarity": 0.5},
                   composite=0.3, passed=False, cost_usd=0.01, duration_ms=1000)
    return EvalReport(config_id="baseline", tasks=[ts], composite=0.3, pass_rate=0.0,
                      total_cost_usd=0.01, total_duration_ms=1000)


def test_generate_candidates_applies_mutations_in_surface():
    baseline = AgentConfig(config_id="baseline", model="sonnet",
                           system_prompt_append="old", allowed_tools=["Read"])

    def fake_gen(prompt):
        return [{"system_prompt_append": "new better instructions",
                 "allowed_tools": ["Read", "Edit"], "note": "x"},
                {"disallowed_tools": ["Bash"]}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=2, generator=fake_gen)
    assert [c.config_id for c in cands] == ["baseline-gen1", "baseline-gen2"]
    assert cands[0].system_prompt_append == "new better instructions"
    assert cands[0].allowed_tools == ["Read", "Edit"]
    assert cands[1].system_prompt_append == "old"          # unchanged -> baseline's
    assert cands[1].disallowed_tools == ["Bash"]


def test_generate_candidates_ignores_out_of_surface_fields():
    baseline = AgentConfig(config_id="b", model="sonnet")

    def fake_gen(prompt):
        return [{"model": "opus", "budget_usd": 99, "system_prompt_append": "z"}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=1, generator=fake_gen)
    assert cands[0].model == "sonnet"      # model NOT changed (out of surface)
    assert cands[0].budget_usd is None     # ignored
    assert cands[0].system_prompt_append == "z"


def test_generate_candidates_fresh_collections():
    baseline = AgentConfig(config_id="b", model="sonnet", allowed_tools=["Read"])
    cands = generate_candidates(baseline, [_report_with_failure()], n=1,
                                generator=lambda p: [{"note": "noop"}])
    cands[0].allowed_tools.append("Write")
    assert baseline.allowed_tools == ["Read"]   # no aliasing


def test_generate_candidates_empty_and_cap():
    baseline = AgentConfig(config_id="b", model="sonnet")
    assert generate_candidates(baseline, [_report_with_failure()], n=2,
                               generator=lambda p: []) == []
    three = generate_candidates(baseline, [_report_with_failure()], n=2,
                                generator=lambda p: [{"note": "1"}, {"note": "2"}, {"note": "3"}])
    assert len(three) == 2                       # capped at n


def test_build_candidate_prompt_includes_failures_and_instructions():
    baseline = AgentConfig(config_id="b", model="sonnet", system_prompt_append="BE TERSE")
    prompt = _build_candidate_prompt(baseline, [_report_with_failure()], 2)
    assert "BE TERSE" in prompt
    assert "tests" in prompt and "slug" in prompt   # failing dim + task surfaced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_generate_candidates.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_candidates'`

- [ ] **Step 3: Write minimal implementation**

In `optimize/candidates.py`, add `import json` to the imports (so the top is `import dataclasses` then `import json`), then append:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_generate_candidates.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/candidates.py tests/test_generate_candidates.py
git commit -m "feat(optimize): failure-driven generate_candidates (instructions + tools)"
```

---

### Task 2: integrate the generator into `run_optimize`

**Files:**
- Modify: `optimize/loop.py`
- Modify: `tests/test_loop.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_loop.py`:
```python
def test_run_optimize_uses_generator(tmp_path):
    baseline = _baseline(tmp_path)

    def fake_gen(prompt):
        return [{"system_prompt_append": "fixed instructions"}]

    out = tmp_path / "runs"
    res = run_optimize(baseline, [], _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out),
                       generator=fake_gen, n_generated=1)
    ids = [c.config_id for c in res.candidates]
    assert "baseline" in ids and "baseline-gen1" in ids   # generated candidate was evaluated
    assert (out / "baseline-gen1").exists()                # its run dir was created


def test_run_optimize_no_generator_unchanged(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out))   # no generator
    assert res.winner_id == "baseline-haiku"
    assert all("gen" not in c.config_id for c in res.candidates)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_loop.py -k generator -v`
Expected: FAIL — `TypeError: run_optimize() got an unexpected keyword argument 'generator'`

- [ ] **Step 3: Write minimal implementation**

Replace the entire body of `run_optimize` in `optimize/loop.py`. The current implementation is:
```python
def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None):
    # de-dup by config_id, baseline first
    all_configs = [baseline]
    seen = {baseline.config_id}
    for c in candidates:
        if c.config_id not in seen:
            all_configs.append(c)
            seen.add(c.config_id)

    results = []
    for cfg in all_configs:
        reports = []
        for i in range(max(1, repeats)):
            reports.append(run_evalset(
                evalset, cfg, runner=runner, judge=judge,
                out_dir=os.path.join(out_dir, cfg.config_id, f"run_{i}")))
        results.append(ranking.aggregate(cfg.config_id, reports))

    result = ranking.build_result(results, baseline.config_id, min_quality=min_quality,
                                  by=by, budget_remaining=budget_remaining,
                                  need_tasks=need_tasks)
    os.makedirs(out_dir, exist_ok=True)
    result.to_json(os.path.join(out_dir, "optimize.json"))

    winner_cfg = next(c for c in all_configs if c.config_id == result.winner_id)
    winner_cfg.save(os.path.join(out_dir, "promoted"))
    if promote_to:
        winner_cfg.save(promote_to)
    return result
```
Replace it with (adds `generator`/`n_generated`; evaluates the baseline first; generates from its reports; then evaluates the rest):
```python
def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None, generator=None, n_generated=0):
    def _run_config(cfg):
        reports = []
        for i in range(max(1, repeats)):
            reports.append(run_evalset(
                evalset, cfg, runner=runner, judge=judge,
                out_dir=os.path.join(out_dir, cfg.config_id, f"run_{i}")))
        return reports

    # 1. baseline first (its reports feed failure-driven generation)
    baseline_reports = _run_config(baseline)
    all_configs = [baseline]
    results = [ranking.aggregate(baseline.config_id, baseline_reports)]
    seen = {baseline.config_id}

    # 2. failure-driven generation off the baseline's reports
    working = list(candidates)
    if n_generated > 0:
        working += candidates_mod.generate_candidates(
            baseline, baseline_reports, n=n_generated, generator=generator)

    # 3. evaluate the rest (deduped; baseline already evaluated)
    for cfg in working:
        if cfg.config_id in seen:
            continue
        seen.add(cfg.config_id)
        all_configs.append(cfg)
        results.append(ranking.aggregate(cfg.config_id, _run_config(cfg)))

    result = ranking.build_result(results, baseline.config_id, min_quality=min_quality,
                                  by=by, budget_remaining=budget_remaining,
                                  need_tasks=need_tasks)
    os.makedirs(out_dir, exist_ok=True)
    result.to_json(os.path.join(out_dir, "optimize.json"))

    winner_cfg = next(c for c in all_configs if c.config_id == result.winner_id)
    winner_cfg.save(os.path.join(out_dir, "promoted"))
    if promote_to:
        winner_cfg.save(promote_to)
    return result
```
Add the candidates import at the top of `optimize/loop.py` (after the existing `from optimize import ranking`):
```python
from optimize import candidates as candidates_mod
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_loop.py -v`
Expected: PASS (all loop tests — the existing ones STILL pass since the no-generator path evaluates the same set)

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 3 skipped)

- [ ] **Step 6: Commit**

```bash
git add optimize/loop.py tests/test_loop.py
git commit -m "feat(optimize): integrate failure-driven generation into run_optimize"
```

---

### Task 3: `--generate N` CLI flag

**Files:**
- Modify: `optimize/cli.py`
- Modify: `tests/test_cli_optimize.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_cli_optimize.py`, add `generate=0` to the `_args` base dict. The current `_args` is:
```python
def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                candidate=None, model_sweep=True, repeats=1, min_quality=None, by="cost",
                out=str(tmp_path / "runs"), promote=None,
                history=str(tmp_path / "no_history"), yes=True,
                budget_remaining=None, budget_config=None, budget_scope="global",
                need_tasks=None)
    base.update(kw)
    return types.SimpleNamespace(**base)
```
Change the base dict's last line to also include `generate=0`:
```python
                need_tasks=None, generate=0)
```
Then APPEND this test:
```python
def test_cmd_optimize_passes_generate_through(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_optimize(*a, **k):
        captured.update(k)
        return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                              improved=False, candidates=[_cr("baseline", 0.8, 0.02)],
                              pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)

    monkeypatch.setattr(cli, "run_optimize", fake_run_optimize)
    cli.cmd_optimize(_args(tmp_path, generate=2, model_sweep=False))
    assert captured["n_generated"] == 2
    assert "baseline" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -k generate -v`
Expected: FAIL — `KeyError: 'n_generated'` (cmd_optimize doesn't pass it yet)

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py` `cmd_optimize`, update the pre-flight config count and the `run_optimize` call. The current tail of `cmd_optimize` is:
```python
    n_cfgs = 1 + len(candidates)
    n_tasks = len(evalset.tasks)
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * n_cfgs * args.repeats
        print(f"Pre-flight: {n_cfgs} configs x {n_tasks} tasks x {args.repeats} repeats; "
              f"est. total ~ {_fmt_cost(total)} (per-task p90, modeled at list prices)",
              file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)
    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    result = run_optimize(baseline, candidates, evalset, repeats=args.repeats,
                          min_quality=args.min_quality, by=args.by, out_dir=args.out,
                          promote_to=args.promote, budget_remaining=budget_remaining,
                          need_tasks=args.need_tasks)
    _print_optimize_result(result, args.out)
```
Replace it with (count generated configs in pre-flight; pass `n_generated`):
```python
    n_cfgs = 1 + len(candidates) + args.generate
    n_tasks = len(evalset.tasks)
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * n_cfgs * args.repeats
        print(f"Pre-flight: {n_cfgs} configs x {n_tasks} tasks x {args.repeats} repeats "
              f"(incl. {args.generate} generated); est. total ~ {_fmt_cost(total)} "
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

    result = run_optimize(baseline, candidates, evalset, repeats=args.repeats,
                          min_quality=args.min_quality, by=args.by, out_dir=args.out,
                          promote_to=args.promote, budget_remaining=budget_remaining,
                          need_tasks=args.need_tasks, n_generated=args.generate)
    _print_optimize_result(result, args.out)
```
> Note: `cmd_optimize` passes only `n_generated`; `run_optimize`/`generate_candidates` default the
> `generator` to the live SDK generator when `n_generated > 0`. Tests inject a fake generator
> directly into `run_optimize`, so they never reach the SDK.

In `main()`, add the `--generate` flag to the `optimize` subparser (after the existing `--need-tasks` line, before `o.set_defaults(func=cmd_optimize)`):
```python
    o.add_argument("--generate", type=int, default=0,
                   help="generate N failure-driven candidates (instructions/tools) via an LLM")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -v`
Expected: PASS (all optimize CLI tests, incl. the new one)

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 3 skipped)

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_optimize.py
git commit -m "feat(optimize): --generate N flag for failure-driven candidates"
```

---

### Task 4: docs + opt-in live smoke

**Files:**
- Modify: `tests/test_live_smoke.py`, `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Add an opt-in live smoke test**

Append to `tests/test_live_smoke.py`:
```python
def test_live_optimize_generate(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK generate smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.evalset import EvalSet
    from optimize.loop import run_optimize

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    baseline = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [{"name": "file", "weight": 1,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    res = run_optimize(baseline, [], evalset, out_dir=str(tmp_path / "runs"), n_generated=1)
    assert res.winner_id in (c.config_id for c in res.candidates)
```

- [ ] **Step 2: Verify the suite (new live test skipped by default)**

Run: `python3.11 -m pytest -q`
Expected: PASS; live tests skipped (e.g. "NN passed, 4 skipped").

- [ ] **Step 3: Update `README.md`** — extend the "Optimizing (cost-first, budget-aware)" subsection

READ `README.md`. In the "Optimizing (cost-first, budget-aware)" subsection, after its code block, append:
```markdown
Add `--generate N` to have an LLM read the baseline's eval failures and propose N candidate
configs (rewritten instructions / adjusted tool lists) — they're evaluated and ranked alongside
the rest:

```bash
tokencast-optimize optimize evalset.yaml --config configs/baseline/ --generate 3
```

The generator only ever changes instructions and tool allow/deny lists (the skills & MCP axes
come later); its proposals run through the same sandbox + ranking as any candidate.
```

- [ ] **Step 4: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Optimize loop (sub-project 3b) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Failure-driven generator (sub-project 4a) — done.** `optimize --generate N` evals the baseline,
feeds its failing dimensions to an LLM that proposes candidate configs (instructions + tool
selection), then evals + ranks them with the rest. Skills & MCP axes are sub-project 4b.
```

- [ ] **Step 5: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` (loop) bullet, add:
```markdown
- `optimize/` (generator) — `candidates.generate_candidates` + `optimize --generate N`: a
  failure-driven LLM generator (behind an injectable seam) that reads the baseline's failing
  dimensions and proposes candidate configs mutating instructions + tool selection, evaluated
  and ranked in the same loop. Sub-project 4a; skills/MCP axes are 4b.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; live tests skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document the failure-driven generator; live smoke"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `generate_candidates` only mutates `system_prompt_append`/`allowed_tools`/`disallowed_tools`; out-of-surface fields in the LLM output are ignored; variants don't alias the baseline; output capped at `n`.
- `run_optimize` evaluates the baseline first, generates from its failures when `n_generated>0`, and evaluates+ranks generated candidates with the rest; `--generate 0` (default) is identical to 3b.
- `tokencast-optimize optimize … --generate N` works end to end, with generated configs in the ranked table and counted in the pre-flight estimate.
- The whole suite runs zero-spend with no `claude-agent-sdk` installed; the default SDK generator is `# pragma: no cover` + one gated live smoke.
