# TokenCast Generator Skills/MCP Axes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the failure-driven candidate generator (4a) to the skills and MCP axes — the LLM proposes skill/server *names* (validated against the 4b-i catalogs, resolved additively onto the baseline), prompted with each failing dimension's definition — plus `--skills-dir`/`--mcp-catalog` CLI flags.

**Architecture:** `generate_candidates`/`_build_candidate_prompt`/`_apply_mutation` in `optimize/candidates.py` gain `evalset` + `skills_catalog` + `mcp_catalog` parameters. `run_optimize` forwards them (with the evalset it already has). `cmd_optimize` resolves the catalogs via `optimize/catalog.py` only when `--generate N > 0` and passes them through. Names-only validation is the safety boundary; the LLM never supplies an MCP `command`. Fully optional: `--generate 0` is byte-identical to 4a.

**Tech Stack:** Python 3.8+, reuses `AgentConfig`, `EvalSet`/`Dimension`/`Check`, `EvalReport`/`TaskScore`, `catalog.available_skills`/`available_mcp`, `run_optimize`, `ranking`. `claude-agent-sdk` only via the pre-existing lazy default generator. `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-generator-skills-mcp-axes-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. A repo security hook flags the four-letter token e-v-a-l immediately followed by an open paren — never write that token in code or tests (this is why the loop helper is named `_run_config`, not the obvious alternative).

**Key facts about reused types:**
- `optimize/candidates.py` (4a) currently defines `model_sweep`, `from_dirs`, `_failing_dimensions(reports) -> [(task_id, dim_name, score)]`, `_build_candidate_prompt(baseline, baseline_reports, n)`, `_apply_mutation(baseline, mut, i)` (honors only `system_prompt_append`/`allowed_tools`/`disallowed_tools`), `generate_candidates(baseline, baseline_reports, *, n=2, generator=None)`, and the `# pragma: no cover` SDK default generator. It imports `dataclasses` then `json`, and `from optimize.config import AgentConfig`.
- `AgentConfig` fields: `config_id, model, system_prompt_append, allowed_tools, disallowed_tools, mcp_servers (dict), skills_source, skills (Optional[List[str]]), budget_usd, max_turns`.
- `optimize/evalset.py`: `EvalSet.tasks: List[EvalTask]`; `EvalTask` has `id`, `dimensions: List[Dimension]`; `Dimension` has `name`, `weight`, `required (bool)`, `checks: List[Check]`, `judge: Optional[str]`, and `is_rule` (`judge is None`); `Check` has `kind` (`command`/`file_exists`/`file_contains`), `cmd`, `path`, `pattern`.
- `optimize/catalog.py` (4b-i): `available_skills(skills_dir=None) -> list[str]` (defaults to `~/.claude/skills`), `available_mcp(catalog_path=None) -> dict` (defaults to `~/.claude.json`).
- `optimize/loop.py`: `run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1, min_quality=None, by="cost", out_dir="runs", promote_to=None, budget_remaining=None, need_tasks=None, generator=None, n_generated=0)`; imports `from optimize import candidates as candidates_mod`. When `n_generated>0` it calls `candidates_mod.generate_candidates(baseline, baseline_reports, n=n_generated, generator=generator)`.
- `optimize/cli.py`: imports `from optimize.candidates import from_dirs, model_sweep` and `from optimize.loop import run_optimize` (NOT `catalog`). `cmd_optimize` computes `n_generated = max(0, args.generate)`, prints a pre-flight, then calls `run_optimize(..., n_generated=n_generated)`. The `optimize` subparser already has `--generate`.
- `tests/test_cli_optimize.py` `_args(tmp_path, **kw)` base dict already ends `need_tasks=None, generate=0)`; the file already imports `cli`, `OptimizeResult`, and a `_cr(...)` helper (used by `test_cmd_optimize_passes_generate_through`).
- `tests/test_loop.py` has `_baseline(tmp_path)`, `_evalset()`, `_runner_cost_by_model(prompt, options, cwd)`, and imports `model_sweep`, `AgentConfig`, `EvalSet`, `run_optimize`.
- `tests/test_generate_candidates.py` imports `from optimize.candidates import generate_candidates, _build_candidate_prompt`, `from optimize.config import AgentConfig`, `from optimize.scorer import EvalReport, TaskScore`, and defines `_report_with_failure()` returning an `EvalReport` whose single `TaskScore` has `task_id="slug"`, `dimension_scores={"tests": 0.0, "clarity": 0.5}`.

---

## File Structure

- Modify: `optimize/candidates.py` — add `_dedup`, `_describe_dimension`, `_format_failing_with_defs`; extend `_build_candidate_prompt`, `_apply_mutation`, `generate_candidates` with `evalset`/`skills_catalog`/`mcp_catalog`.
- Modify: `optimize/loop.py` — `run_optimize` gains `skills_catalog`/`mcp_catalog`; forwards them + `evalset` to `generate_candidates`.
- Modify: `optimize/cli.py` — `from optimize import catalog`; `--skills-dir`/`--mcp-catalog` flags; resolve catalogs when `--generate>0`; pass through.
- Modify tests: `tests/test_generate_candidates.py`, `tests/test_loop.py`, `tests/test_cli_optimize.py`, `tests/test_live_smoke.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.py`, `tokencast.html`, `budget.py`, `optimize/staging.py`, `optimize/catalog.py`.

---

### Task 1: skills/MCP mutation surface + prompt enrichment (`candidates.py`)

**Files:**
- Modify: `optimize/candidates.py`
- Modify: `tests/test_generate_candidates.py`

- [ ] **Step 1: Write the failing tests**

First, update the imports at the top of `tests/test_generate_candidates.py`. Replace:
```python
from optimize.candidates import generate_candidates, _build_candidate_prompt
from optimize.config import AgentConfig
from optimize.scorer import EvalReport, TaskScore
```
with:
```python
from optimize.candidates import generate_candidates, _build_candidate_prompt, _apply_mutation
from optimize.config import AgentConfig
from optimize.evalset import EvalSet
from optimize.scorer import EvalReport, TaskScore
```

Then APPEND these tests:
```python
def test_apply_mutation_skills_additive_validated():
    baseline = AgentConfig(config_id="b", model="sonnet", skills=["base"])
    cand = _apply_mutation(baseline, {"skills": ["pdf", "nope", "base"]}, 0,
                           skills_catalog=["pdf", "base"], mcp_catalog={})
    assert cand.skills == ["base", "pdf"]      # union, deduped, unknown 'nope' dropped
    assert baseline.skills == ["base"]          # no aliasing


def test_apply_mutation_skills_promotes_none_baseline():
    baseline = AgentConfig(config_id="b", model="sonnet")   # skills None
    cand = _apply_mutation(baseline, {"skills": ["pdf"]}, 0,
                           skills_catalog=["pdf"], mcp_catalog={})
    assert cand.skills == ["pdf"]


def test_apply_mutation_mcp_resolved_from_catalog():
    baseline = AgentConfig(config_id="b", model="sonnet",
                           mcp_servers={"keep": {"command": "x"}})
    cat = {"playwright": {"command": "npx", "args": ["pw"]}}
    cand = _apply_mutation(baseline, {"mcp": ["playwright", "ghost"]}, 0,
                           skills_catalog=[], mcp_catalog=cat)
    assert cand.mcp_servers == {"keep": {"command": "x"},
                                "playwright": {"command": "npx", "args": ["pw"]}}
    assert "ghost" not in cand.mcp_servers       # unknown dropped


def test_apply_mutation_empty_catalog_drops_axes():
    baseline = AgentConfig(config_id="b", model="sonnet")
    cand = _apply_mutation(baseline, {"skills": ["pdf"], "mcp": ["x"],
                                      "system_prompt_append": "z"}, 0)
    assert cand.skills is None                   # nothing valid -> untouched
    assert cand.mcp_servers == {}
    assert cand.system_prompt_append == "z"       # other axes still apply


def test_build_prompt_required_first_with_defs_and_catalogs():
    baseline = AgentConfig(config_id="b", model="sonnet")
    evalset = EvalSet.from_dict({"tasks": [{"id": "slug", "prompt": "p", "dimensions": [
        {"name": "clarity", "judge": "is it clear? 0-1"},
        {"name": "tests", "weight": 1, "required": True,
         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})
    report = EvalReport(config_id="b", tasks=[TaskScore(
        task_id="slug", dimension_scores={"clarity": 0.5, "tests": 0.0},
        composite=0.25, passed=False, cost_usd=0.01, duration_ms=10)],
        composite=0.25, pass_rate=0.0, total_cost_usd=0.01, total_duration_ms=10)
    prompt = _build_candidate_prompt(baseline, [report], 2, evalset=evalset,
                                     skills_catalog=["pdf", "docx"],
                                     mcp_catalog={"playwright": {}})
    assert prompt.index("tests") < prompt.index("clarity")   # required dim listed first
    assert "[REQUIRED]" in prompt
    assert "file_exists path=pong.txt" in prompt             # rule summary
    assert "is it clear? 0-1" in prompt                      # judge rubric
    assert "pdf, docx" in prompt
    assert "playwright" in prompt


def test_build_prompt_empty_catalogs_say_none():
    baseline = AgentConfig(config_id="b", model="sonnet")
    prompt = _build_candidate_prompt(baseline, [_report_with_failure()], 1)
    assert "(none available)" in prompt


def test_generate_candidates_skills_mcp_end_to_end():
    baseline = AgentConfig(config_id="b", model="sonnet")

    def fake_gen(prompt):
        return [{"skills": ["pdf"], "mcp": ["playwright"], "note": "x"}]

    cands = generate_candidates(baseline, [_report_with_failure()], n=1, generator=fake_gen,
                                skills_catalog=["pdf"],
                                mcp_catalog={"playwright": {"command": "npx"}})
    assert cands[0].skills == ["pdf"]
    assert cands[0].mcp_servers == {"playwright": {"command": "npx"}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_generate_candidates.py -v`
Expected: FAIL — `ImportError: cannot import name '_apply_mutation'` (and, once that's added, `_apply_mutation()`/`_build_candidate_prompt()`/`generate_candidates()` reject the new keyword args).

- [ ] **Step 3: Write the implementation**

In `optimize/candidates.py`, add these three helpers (place them just above the existing `_failing_dimensions`):
```python
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
```

Replace the entire existing `_build_candidate_prompt` with:
```python
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
```

Replace the entire existing `_apply_mutation` with:
```python
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
        resolved = {name: (mcp_catalog or {})[name] for name in mut["mcp"]
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
```

Replace the entire existing `generate_candidates` with:
```python
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
```

(Leave `model_sweep`, `from_dirs`, `_failing_dimensions`, `_default_candidate_generator`, and `_generate_candidates_sdk` unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_generate_candidates.py -v`
Expected: PASS (the 5 pre-existing 4a tests + the 7 new ones).

- [ ] **Step 5: Commit**

```bash
git add optimize/candidates.py tests/test_generate_candidates.py
git commit -m "feat(optimize): generator skills/MCP axes + dimension-enriched prompt"
```

---

### Task 2: thread catalogs + evalset through `run_optimize`

**Files:**
- Modify: `optimize/loop.py`
- Modify: `tests/test_loop.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/test_loop.py`:
```python
def test_run_optimize_passes_catalogs_to_generator(tmp_path):
    baseline = _baseline(tmp_path)
    seen = {}

    def fake_gen(prompt):
        seen["prompt"] = prompt
        return [{"skills": ["pdf"]}]

    out = tmp_path / "runs"
    run_optimize(baseline, [], _evalset(), runner=_runner_cost_by_model,
                 judge=lambda p: 1.0, out_dir=str(out), generator=fake_gen,
                 n_generated=1, skills_catalog=["pdf"], mcp_catalog={"pw": {}})
    assert "pdf" in seen["prompt"]               # skills catalog reached the prompt
    assert "pw" in seen["prompt"]                # mcp catalog reached the prompt
    assert (out / "baseline-gen1").exists()      # the generated candidate was evaluated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_loop.py -k catalogs -v`
Expected: FAIL — `TypeError: run_optimize() got an unexpected keyword argument 'skills_catalog'`

- [ ] **Step 3: Write the implementation**

In `optimize/loop.py`, change the `run_optimize` signature line from:
```python
def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None, generator=None, n_generated=0):
```
to:
```python
def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None, generator=None, n_generated=0,
                 skills_catalog=None, mcp_catalog=None):
```
Then change the generation call from:
```python
    if n_generated > 0:
        working += candidates_mod.generate_candidates(
            baseline, baseline_reports, n=n_generated, generator=generator)
```
to:
```python
    if n_generated > 0:
        working += candidates_mod.generate_candidates(
            baseline, baseline_reports, n=n_generated, generator=generator,
            evalset=evalset, skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_loop.py -v`
Expected: PASS (all loop tests — existing ones still pass; the new params are inert when unused).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (4 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/loop.py tests/test_loop.py
git commit -m "feat(optimize): run_optimize forwards skills/MCP catalogs + evalset to the generator"
```

---

### Task 3: `--skills-dir` / `--mcp-catalog` CLI flags

**Files:**
- Modify: `optimize/cli.py`
- Modify: `tests/test_cli_optimize.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli_optimize.py`, add `skills_dir` and `mcp_catalog` to the `_args` base dict. Change its last line from:
```python
                need_tasks=None, generate=0)
```
to:
```python
                need_tasks=None, generate=0, skills_dir=None, mcp_catalog=None)
```
Then APPEND these tests:
```python
def test_cmd_optimize_resolves_and_passes_catalogs(tmp_path, monkeypatch):
    sdir = tmp_path / "skills"
    (sdir / "myskill").mkdir(parents=True)
    (sdir / "myskill" / "SKILL.md").write_text("---\ndescription: x\n---\n")
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"pw": {"command": "npx"}}')

    captured = {}

    def fake_run_optimize(*a, **k):
        captured.update(k)
        return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                              improved=False, candidates=[_cr("baseline", 0.8, 0.02)],
                              pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)

    monkeypatch.setattr(cli, "run_optimize", fake_run_optimize)
    cli.cmd_optimize(_args(tmp_path, generate=1, model_sweep=False,
                           skills_dir=str(sdir), mcp_catalog=str(mcp)))
    assert captured["skills_catalog"] == ["myskill"]
    assert captured["mcp_catalog"] == {"pw": {"command": "npx"}}
    assert captured["n_generated"] == 1


def test_cmd_optimize_no_generate_skips_catalogs(tmp_path, monkeypatch):
    captured = {}

    def fake_run_optimize(*a, **k):
        captured.update(k)
        return OptimizeResult(baseline_id="baseline", floor=0.8, winner_id="baseline",
                              improved=False, candidates=[_cr("baseline", 0.8, 0.02)],
                              pareto=["baseline"], cost_delta_pct=0.0, quality_delta=0.0)

    monkeypatch.setattr(cli, "run_optimize", fake_run_optimize)
    cli.cmd_optimize(_args(tmp_path, generate=0, model_sweep=False))
    assert captured["skills_catalog"] is None
    assert captured["mcp_catalog"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -k catalog -v`
Expected: FAIL — `KeyError: 'skills_catalog'` (cmd_optimize neither resolves nor passes them yet).

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py`, add the catalog import. Change:
```python
from optimize.candidates import from_dirs, model_sweep
from optimize.loop import run_optimize
```
to:
```python
from optimize.candidates import from_dirs, model_sweep
from optimize.loop import run_optimize
from optimize import catalog
```

In `cmd_optimize`, the current tail is:
```python
    result = run_optimize(baseline, candidates, evalset, repeats=args.repeats,
                          min_quality=args.min_quality, by=args.by, out_dir=args.out,
                          promote_to=args.promote, budget_remaining=budget_remaining,
                          need_tasks=args.need_tasks, n_generated=n_generated)
    _print_optimize_result(result, args.out)
```
Replace it with (resolve the catalogs only when generating, then pass them through):
```python
    skills_catalog = mcp_catalog = None
    if n_generated > 0:
        skills_catalog = catalog.available_skills(args.skills_dir)
        mcp_catalog = catalog.available_mcp(args.mcp_catalog)

    result = run_optimize(baseline, candidates, evalset, repeats=args.repeats,
                          min_quality=args.min_quality, by=args.by, out_dir=args.out,
                          promote_to=args.promote, budget_remaining=budget_remaining,
                          need_tasks=args.need_tasks, n_generated=n_generated,
                          skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)
    _print_optimize_result(result, args.out)
```

In `main()`, add the two flags to the `optimize` subparser, immediately after the existing `--generate` line and before `o.set_defaults(func=cmd_optimize)`:
```python
    o.add_argument("--skills-dir", default=None,
                   help="skills catalog dir for --generate candidates (default ~/.claude/skills)")
    o.add_argument("--mcp-catalog", default=None,
                   help="MCP catalog JSON for --generate candidates (default ~/.claude.json)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -v`
Expected: PASS (all optimize CLI tests, incl. the 2 new ones).

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (4 skipped).

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_optimize.py
git commit -m "feat(optimize): --skills-dir/--mcp-catalog flags feed the generator's axes"
```

---

### Task 4: docs + opt-in live smoke

**Files:**
- Modify: `tests/test_live_smoke.py`, `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Add an opt-in live smoke test**

APPEND to `tests/test_live_smoke.py`:
```python
def test_live_optimize_generate_skills_axis(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK skills-axis smoke (spends a little)")

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
         "dimensions": [{"name": "file", "weight": 1, "required": True,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    res = run_optimize(baseline, [], evalset, out_dir=str(tmp_path / "runs"), n_generated=1,
                       skills_catalog=["nonexistent-skill"], mcp_catalog={})
    assert res.winner_id in (c.config_id for c in res.candidates)
```

- [ ] **Step 2: Verify the suite (new live test skipped by default)**

Run: `python3.11 -m pytest -q`
Expected: PASS; live tests skipped (e.g. "NN passed, 5 skipped").

- [ ] **Step 3: Update `README.md`**

READ `README.md`. In the "Optimizer tier (preview)" section, find the paragraph added in 4b-i that begins "A config can also declare which **skills** it uses". Immediately after that paragraph, append:
```markdown
With `--generate N`, the generator now tunes those axes too: it's shown each failing dimension's
definition (rubric / rule) and the skills + MCP servers you have available, and may propose adding
some to a candidate. Point it at a specific catalog with `--skills-dir DIR` (default
`~/.claude/skills`) and `--mcp-catalog FILE` (default `~/.claude.json`); proposed names are
validated against those catalogs and added on top of the baseline's.
```

- [ ] **Step 4: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Skills/MCP wiring (sub-project 4b-i) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Generator skills/MCP axes (sub-project 4b-ii) — done.** `optimize --generate N` now proposes
skills and MCP servers (by name, validated against the `--skills-dir`/`--mcp-catalog` catalogs and
added onto the baseline), prompted with each failing dimension's definition. Task decomposition is
sub-project 5.
```

- [ ] **Step 5: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` (skills wiring) bullet, add:
```markdown
- `optimize/` (generator axes) — `generate_candidates` now also mutates the skills + MCP axes
  (names validated against `catalog.available_skills`/`available_mcp`, resolved additively onto
  the baseline) and enriches its prompt with each failing dimension's definition (required dims
  first). `optimize --skills-dir/--mcp-catalog` feed the catalogs. Sub-project 4b-ii.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (live tests skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document generator skills/MCP axes (4b-ii); live smoke"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `_apply_mutation` validates skills/MCP names against the catalogs (unknowns dropped), resolves MCP names to their catalog configs (never the LLM's), and unions both onto the baseline; a `skills=None` baseline is promoted to the proposed names; no aliasing.
- `_build_candidate_prompt` surfaces failing required dimensions first (tagged `[REQUIRED]`) with their rubric/rule text, plus the available skill/MCP names; `evalset=None` falls back to the 4a plain lines; empty catalogs render `(none available)`.
- `run_optimize` forwards `skills_catalog`/`mcp_catalog`/`evalset` to `generate_candidates`; `cmd_optimize` resolves the catalogs only when `--generate>0` and passes them through; `--generate 0` is byte-identical to 4a.
- The whole suite runs zero-spend with no `claude-agent-sdk` installed; the default SDK generator stays `# pragma: no cover` + one gated live smoke.
- `tokencast.py`/`tokencast.html`/`budget.py`/`optimize/staging.py`/`optimize/catalog.py` unchanged.
