# TokenCast Optimize Loop (budget-aware) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the closed optimize loop — eval a baseline + candidate configs, rank cost-first under a quality floor (Pareto surfaced), recommend/promote a winner — and make it optionally budget-aware (frame the win as runway gained against a cap, with a `--need-tasks` fit verdict).

**Architecture:** New `optimize/candidates.py` (config sources), `optimize/ranking.py` (pure: aggregate/select/Pareto/budget pass), `optimize/loop.py` (orchestration over `run_evalset`), an `AgentConfig.save` addition, and an `optimize` CLI subcommand. Budget is fully optional: the loop takes a plain `budget_remaining` number (the CLI resolves it from the 3a ledger); with none, the loop is the lean core.

**Tech Stack:** Python 3.8+, reuses sub-project 1 (`AgentConfig`/`pricing`/`tokencast.pct`), 2 (`run_evalset`/`EvalReport`/`EvalSet`), 3a (`budget.runway_tasks`/`BudgetConfig`/`status`). `pyyaml` (heavy tier) for `AgentConfig.save`. `pytest`. Money paths behind injectable runner/judge seams → zero-spend tests.

**Reference spec:** `docs/superpowers/specs/2026-06-04-optimize-loop-design.md`

**Environment note:** use `python3.11 -m pytest ...` (`python`/`python3` lack pytest). End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

**Key facts about reused types:**
- `EvalReport` (optimize/scorer.py) fields: `config_id, tasks, composite, pass_rate, total_cost_usd, total_duration_ms`.
- `run_evalset(evalset, config, *, runner=None, judge=None, out_dir="runs") -> EvalReport`.
- `AgentConfig` (optimize/config.py) fields: `config_id, model, system_prompt_append, allowed_tools, disallowed_tools, mcp_servers, skills_source, budget_usd, max_turns`; `config.py` imports `json`, `os`, and a `yaml` shim at module top.
- `budget.runway_tasks(remaining, per_task_cost) -> int`; `budget.BudgetConfig.load`, `budget.collect_spend`, `budget.status`.
- `tokencast.pct(values, q)`, `tokencast.money(x)`; `optimize/cli.py` already has `estimate_cost`, `_fmt_cost`, `AgentConfig` import, `cmd_run`, `cmd_eval_run`, `cmd_eval_init`, and `main()`.

---

## File Structure

- Modify: `optimize/config.py` — add `AgentConfig.save(path)`.
- Create: `optimize/candidates.py` — `model_sweep`, `from_dirs`.
- Create: `optimize/ranking.py` — `CandidateResult`, `OptimizeResult`, `aggregate`, `select`, `pareto`, `build_result`, `apply_budget`.
- Create: `optimize/loop.py` — `run_optimize`.
- Modify: `optimize/cli.py` — `optimize` subcommand + `_print_optimize_result`.
- Create tests: `tests/test_config_save.py`, `tests/test_candidates.py`, `tests/test_ranking.py`, `tests/test_loop.py`, `tests/test_cli_optimize.py`; append to `tests/test_live_smoke.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.py`, `tokencast.html`, `budget.py`.

---

### Task 1: `AgentConfig.save`

**Files:**
- Modify: `optimize/config.py`
- Test: `tests/test_config_save.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_save.py`:
```python
from optimize.config import AgentConfig


def test_save_roundtrips(tmp_path):
    cfg = AgentConfig(
        config_id="baseline-haiku", model="haiku",
        system_prompt_append="Be terse.", allowed_tools=["Read", "Edit"],
        disallowed_tools=["Bash"], mcp_servers={"pw": {"command": "npx"}},
        skills_source=None, budget_usd=2.0, max_turns=30)
    dest = tmp_path / "out"
    cfg.save(str(dest))

    back = AgentConfig.load(str(dest))
    assert back.model == "haiku"
    assert back.system_prompt_append == "Be terse."
    assert back.allowed_tools == ["Read", "Edit"]
    assert back.disallowed_tools == ["Bash"]
    assert back.mcp_servers == {"pw": {"command": "npx"}}
    assert back.budget_usd == 2.0
    assert back.max_turns == 30


def test_save_minimal_omits_empty_files(tmp_path):
    import os
    cfg = AgentConfig(config_id="min", model="sonnet")
    dest = tmp_path / "min"
    cfg.save(str(dest))
    assert os.path.exists(os.path.join(str(dest), "metadata.yaml"))
    assert not os.path.exists(os.path.join(str(dest), "instructions.md"))
    assert not os.path.exists(os.path.join(str(dest), "tools.json"))
    back = AgentConfig.load(str(dest))
    assert back.model == "sonnet"
    assert back.system_prompt_append == ""
    assert back.allowed_tools == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_config_save.py -v`
Expected: FAIL — `AttributeError: 'AgentConfig' object has no attribute 'save'`

- [ ] **Step 3: Write minimal implementation**

Add this method to the `AgentConfig` class in `optimize/config.py` (after `to_sdk_options`):
```python
    def save(self, path):
        """Serialize back to a config dir (round-trips with load). Needs pyyaml."""
        if yaml is None:
            raise RuntimeError("pyyaml is required to save a config; install tokencast[optimize]")
        os.makedirs(path, exist_ok=True)
        meta = {"config_id": self.config_id, "model": self.model}
        if self.budget_usd is not None:
            meta["budget_usd"] = self.budget_usd
        if self.max_turns is not None:
            meta["max_turns"] = self.max_turns
        with open(os.path.join(path, "metadata.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(meta, fh, sort_keys=False)
        if self.system_prompt_append:
            with open(os.path.join(path, "instructions.md"), "w", encoding="utf-8") as fh:
                fh.write(self.system_prompt_append)
        tools = {}
        if self.allowed_tools:
            tools["allowed_tools"] = list(self.allowed_tools)
        if self.disallowed_tools:
            tools["disallowed_tools"] = list(self.disallowed_tools)
        if self.mcp_servers:
            tools["mcp_servers"] = dict(self.mcp_servers)
        if tools:
            with open(os.path.join(path, "tools.json"), "w", encoding="utf-8") as fh:
                json.dump(tools, fh, indent=2)
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_config_save.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/config.py tests/test_config_save.py
git commit -m "feat(optimize): AgentConfig.save serializes a config dir (round-trips load)"
```

---

### Task 2: candidate sources

**Files:**
- Create: `optimize/candidates.py`
- Test: `tests/test_candidates.py`

- [ ] **Step 1: Write the failing test**

`tests/test_candidates.py`:
```python
from optimize.candidates import from_dirs, model_sweep
from optimize.config import AgentConfig


def test_model_sweep_makes_variants_skipping_baseline_model():
    baseline = AgentConfig(config_id="baseline", model="sonnet")
    variants = model_sweep(baseline)
    ids = sorted(v.config_id for v in variants)
    models = sorted(v.model for v in variants)
    assert ids == ["baseline-haiku", "baseline-opus"]   # sonnet skipped
    assert models == ["haiku", "opus"]
    # variant keeps other baseline fields
    assert all(v.system_prompt_append == baseline.system_prompt_append for v in variants)


def test_model_sweep_custom_models():
    baseline = AgentConfig(config_id="b", model="opus")
    variants = model_sweep(baseline, models=("opus", "haiku"))
    assert [v.config_id for v in variants] == ["b-haiku"]   # opus == baseline, skipped


def test_from_dirs_loads_configs(tmp_path):
    d = tmp_path / "c1"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: haiku\n")
    configs = from_dirs([str(d)])
    assert len(configs) == 1
    assert configs[0].model == "haiku"
    assert configs[0].config_id == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.candidates'`

- [ ] **Step 3: Write minimal implementation**

`optimize/candidates.py`:
```python
"""Candidate config sources for the optimize loop. A candidate is an AgentConfig with a
distinct config_id. Sub-project 4's generators produce AgentConfigs the same way."""
import dataclasses

from optimize.config import AgentConfig

_DEFAULT_MODELS = ("opus", "sonnet", "haiku")


def model_sweep(baseline, models=_DEFAULT_MODELS):
    """One variant per model, skipping the baseline's own model (a redundant re-eval)."""
    out = []
    for m in models:
        if m == baseline.model:
            continue
        out.append(dataclasses.replace(baseline, model=m,
                                       config_id=f"{baseline.config_id}-{m}"))
    return out


def from_dirs(paths):
    """Load an AgentConfig from each config dir."""
    return [AgentConfig.load(p) for p in paths]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_candidates.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/candidates.py tests/test_candidates.py
git commit -m "feat(optimize): candidate sources (model_sweep + from_dirs)"
```

---

### Task 3: ranking core (aggregate / select / Pareto / build_result)

**Files:**
- Create: `optimize/ranking.py`
- Test: `tests/test_ranking.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ranking.py`:
```python
import json

from optimize.ranking import (CandidateResult, OptimizeResult, aggregate, select,
                               pareto, build_result)
from optimize.scorer import EvalReport


def _cr(cid, quality, cost, dur=1000.0, pass_rate=1.0):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=pass_rate,
                           cost_usd=cost, duration_ms=dur, repeats=1,
                           cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality)


def test_aggregate_medians_and_p90(tmp_path):
    reports = [
        EvalReport(config_id="b", tasks=[], composite=0.8, pass_rate=1.0,
                   total_cost_usd=0.010, total_duration_ms=1000),
        EvalReport(config_id="b", tasks=[], composite=0.6, pass_rate=0.5,
                   total_cost_usd=0.030, total_duration_ms=3000),
        EvalReport(config_id="b", tasks=[], composite=0.7, pass_rate=1.0,
                   total_cost_usd=0.020, total_duration_ms=2000),
    ]
    cr = aggregate("b", reports)
    assert cr.config_id == "b"
    assert abs(cr.quality - 0.7) < 1e-9            # median composite
    assert abs(cr.pass_rate - (2.5 / 3)) < 1e-9    # mean pass_rate
    assert abs(cr.cost_usd - 0.028) < 1e-9         # p90 of [.01,.02,.03] = .028
    assert cr.repeats == 3
    assert cr.cost_min == 0.010 and cr.cost_max == 0.030


def test_select_cost_first_under_floor():
    cands = [_cr("baseline", 0.9, 0.020), _cr("cheap-good", 0.9, 0.010),
             _cr("cheap-bad", 0.4, 0.001)]
    winner_id, improved = select(cands, "baseline", floor=0.9)
    assert winner_id == "cheap-good"   # cheapest at quality >= 0.9 (cheap-bad excluded)
    assert improved is True


def test_select_empty_eligible_falls_back_to_baseline():
    cands = [_cr("baseline", 0.5, 0.02), _cr("c", 0.6, 0.01)]
    winner_id, improved = select(cands, "baseline", floor=0.95)
    assert winner_id == "baseline"
    assert improved is False


def test_select_time_first():
    cands = [_cr("a", 0.9, 0.01, dur=5000), _cr("b", 0.9, 0.05, dur=1000)]
    winner_id, _ = select(cands, "a", floor=0.9, by="time")
    assert winner_id == "b"            # fastest meeting floor


def test_pareto_frontier():
    cands = [_cr("cheapbad", 0.5, 0.001), _cr("mid", 0.8, 0.010),
             _cr("dear-good", 0.9, 0.050), _cr("dominated", 0.7, 0.060)]
    front = set(pareto(cands))
    assert "cheapbad" in front and "mid" in front and "dear-good" in front
    assert "dominated" not in front    # mid is cheaper AND higher quality


def test_build_result_deltas_and_json(tmp_path):
    cands = [_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.winner_id == "baseline-haiku"
    assert res.floor == 0.8
    assert abs(res.cost_delta_pct - (-50.0)) < 1e-9
    assert res.quality_delta == 0.0
    assert res.budget_remaining is None   # no budget pass
    p = tmp_path / "optimize.json"
    res.to_json(str(p))
    data = json.loads(p.read_text())
    assert data["winner_id"] == "baseline-haiku"
    assert data["candidates"][0]["config_id"] in ("baseline", "baseline-haiku")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_ranking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.ranking'`

- [ ] **Step 3: Write minimal implementation**

`optimize/ranking.py`:
```python
"""Pure ranking for the optimize loop: aggregate repeats, quality floor, Pareto, cost-first
selection, and (in Task 4) the optional budget pass. No eval; no I/O beyond to_json."""
import dataclasses
import json
from dataclasses import dataclass, field
from typing import List, Optional

import tokencast


@dataclass
class CandidateResult:
    config_id: str
    quality: float
    pass_rate: float
    cost_usd: float
    duration_ms: float
    repeats: int
    cost_min: float
    cost_max: float
    quality_min: float
    quality_max: float


@dataclass
class OptimizeResult:
    baseline_id: str
    floor: float
    winner_id: str
    improved: bool
    candidates: List[CandidateResult]
    pareto: List[str]
    cost_delta_pct: float
    quality_delta: float

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(self), fh, indent=2)
        return path


def aggregate(config_id, reports):
    composites = [r.composite for r in reports]
    costs = [r.total_cost_usd for r in reports]
    durations = [r.total_duration_ms for r in reports]
    pass_rates = [r.pass_rate for r in reports]
    n = len(reports) or 1
    return CandidateResult(
        config_id=config_id,
        quality=tokencast.pct(composites, 0.5),
        pass_rate=sum(pass_rates) / n,
        cost_usd=tokencast.pct(costs, 0.9),
        duration_ms=tokencast.pct(durations, 0.9),
        repeats=len(reports),
        cost_min=min(costs) if costs else 0.0,
        cost_max=max(costs) if costs else 0.0,
        quality_min=min(composites) if composites else 0.0,
        quality_max=max(composites) if composites else 0.0,
    )


def _by_id(candidates, cid):
    for c in candidates:
        if c.config_id == cid:
            return c
    raise KeyError(cid)


def select(candidates, baseline_id, floor, by="cost"):
    eligible = [c for c in candidates if c.quality >= floor]
    if not eligible:
        return baseline_id, False
    if by == "time":
        keyfn = lambda c: (c.duration_ms, c.cost_usd, -c.quality, c.config_id)
    else:
        keyfn = lambda c: (c.cost_usd, c.duration_ms, -c.quality, c.config_id)
    winner = min(eligible, key=keyfn)
    return winner.config_id, (winner.config_id != baseline_id)


def pareto(candidates):
    front = []
    for a in candidates:
        dominated = any(
            b is not a and b.cost_usd <= a.cost_usd and b.quality >= a.quality
            and (b.cost_usd < a.cost_usd or b.quality > a.quality)
            for b in candidates)
        if not dominated:
            front.append(a.config_id)
    return front


def build_result(candidates, baseline_id, min_quality=None, by="cost"):
    baseline = _by_id(candidates, baseline_id)
    floor = min_quality if min_quality is not None else baseline.quality
    winner_id, improved = select(candidates, baseline_id, floor, by=by)
    winner = _by_id(candidates, winner_id)
    cost_delta_pct = ((winner.cost_usd - baseline.cost_usd) / baseline.cost_usd * 100
                      if baseline.cost_usd else 0.0)
    return OptimizeResult(
        baseline_id=baseline_id, floor=floor, winner_id=winner_id, improved=improved,
        candidates=candidates, pareto=pareto(candidates),
        cost_delta_pct=cost_delta_pct, quality_delta=winner.quality - baseline.quality)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_ranking.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/ranking.py tests/test_ranking.py
git commit -m "feat(optimize): ranking core (aggregate/select/pareto/build_result)"
```

---

### Task 4: ranking budget pass (runway + fit)

**Files:**
- Modify: `optimize/ranking.py`
- Modify: `tests/test_ranking.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ranking.py`:
```python
from optimize.ranking import apply_budget


def test_build_result_with_budget_runway_and_gain():
    cands = [_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)]
    res = build_result(cands, "baseline", budget_remaining=100.0)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline"].runway == 5000        # 100 / 0.02
    assert by["baseline-haiku"].runway == 10000  # 100 / 0.01
    assert res.budget_remaining == 100.0
    assert res.runway_gain == 5000               # winner(haiku) 10000 - baseline 5000


def test_build_result_with_need_tasks_fit_verdict():
    cands = [_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)]
    res = build_result(cands, "baseline", budget_remaining=100.0, need_tasks=8000)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline"].fits is False          # 0.02*8000 = 160 > 100
    assert by["baseline-haiku"].fits is True      # 0.01*8000 = 80 <= 100
    assert res.need_tasks == 8000
    assert res.winner_fits is True               # winner is haiku


def test_build_result_no_budget_leaves_fields_none():
    cands = [_cr("baseline", 0.8, 0.020), _cr("c", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.budget_remaining is None and res.runway_gain is None
    assert all(c.runway is None and c.fits is None for c in res.candidates)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_ranking.py -k "budget or need_tasks or no_budget" -v`
Expected: FAIL — `ImportError: cannot import name 'apply_budget'` (and `build_result` has no `budget_remaining` kwarg)

- [ ] **Step 3: Write minimal implementation**

In `optimize/ranking.py`: (a) add `import budget` to the imports block (after `import tokencast`); (b) add the two budget fields to `CandidateResult` (after `quality_max`); (c) add the four budget fields to `OptimizeResult` (after `quality_delta`); (d) add `apply_budget`; (e) extend `build_result`'s signature + body.

(a) imports:
```python
import tokencast
import budget
```

(b) `CandidateResult` — append these fields (they have defaults, so they go after the required ones):
```python
    runway: Optional[int] = None
    fits: Optional[bool] = None
```

(c) `OptimizeResult` — append these fields after `quality_delta`:
```python
    budget_remaining: Optional[float] = None
    need_tasks: Optional[int] = None
    runway_gain: Optional[int] = None
    winner_fits: Optional[bool] = None
```

(d) add this function (after `build_result`):
```python
def apply_budget(result, budget_remaining, need_tasks=None):
    """Populate runway / fits / runway_gain / winner_fits in place. Pure (uses budget.runway_tasks)."""
    result.budget_remaining = budget_remaining
    result.need_tasks = need_tasks
    for c in result.candidates:
        c.runway = budget.runway_tasks(budget_remaining, c.cost_usd)
        if need_tasks is not None:
            c.fits = (c.cost_usd * need_tasks) <= budget_remaining
    winner = _by_id(result.candidates, result.winner_id)
    base = _by_id(result.candidates, result.baseline_id)
    result.runway_gain = winner.runway - base.runway
    if need_tasks is not None:
        result.winner_fits = winner.fits
    return result
```

(e) change `build_result`'s signature and add the budget pass. Replace:
```python
def build_result(candidates, baseline_id, min_quality=None, by="cost"):
```
with:
```python
def build_result(candidates, baseline_id, min_quality=None, by="cost",
                 budget_remaining=None, need_tasks=None):
```
and, right before the `return OptimizeResult(...)`, capture the result in a variable and apply the budget pass. Replace the final `return OptimizeResult(...)` block with:
```python
    result = OptimizeResult(
        baseline_id=baseline_id, floor=floor, winner_id=winner_id, improved=improved,
        candidates=candidates, pareto=pareto(candidates),
        cost_delta_pct=cost_delta_pct, quality_delta=winner.quality - baseline.quality)
    if budget_remaining is not None:
        apply_budget(result, budget_remaining, need_tasks)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_ranking.py -v`
Expected: PASS (all ranking tests — core + budget)

- [ ] **Step 5: Commit**

```bash
git add optimize/ranking.py tests/test_ranking.py
git commit -m "feat(optimize): budget pass (runway, fit, runway-gain) in ranking"
```

---

### Task 5: the loop (`run_optimize`)

**Files:**
- Create: `optimize/loop.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Write the failing test**

`tests/test_loop.py`:
```python
import json
import os

from optimize.candidates import model_sweep
from optimize.config import AgentConfig
from optimize.evalset import EvalSet
from optimize.loop import run_optimize


def _baseline(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(d))


def _evalset():
    return EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok? 0-1"}]}]})


def _runner_cost_by_model(prompt, options, cwd):
    # same tokens for every model -> pricing makes haiku cheapest, opus dearest
    model = options["model"]
    return {"turns": [{"model": model, "content": []}],
            "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                               "cache_creation_input_tokens": 0,
                                               "cache_read_input_tokens": 0}},
                       "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                       "result_text": "done"}}


def test_run_optimize_picks_cheapest_meeting_floor(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)   # baseline-opus, baseline-haiku
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out))
    assert res.winner_id == "baseline-haiku"     # cheapest; all quality 1.0
    assert res.improved is True
    assert (out / "optimize.json").exists()
    assert (out / "promoted" / "metadata.yaml").exists()
    promoted = AgentConfig.load(str(out / "promoted"))
    assert promoted.model == "haiku"


def test_run_optimize_quality_floor_excludes_cheap_but_bad(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)   # baseline-opus, baseline-haiku

    # runner stamps the model into the final output so the judge can score per-model
    def runner(prompt, options, cwd):
        model = options["model"]
        return {"turns": [{"model": model, "content": []}],
                "result": {"model_usage": {model: {"input_tokens": 0, "output_tokens": 1000,
                                                   "cache_creation_input_tokens": 0,
                                                   "cache_read_input_tokens": 0}},
                           "num_turns": 1, "duration_ms": 1000, "total_cost_usd": 0.0,
                           "result_text": f"ran with {model}"}}

    # haiku is cheapest but scores below the floor -> must be excluded
    def judge(prompt):
        return 0.2 if "haiku" in prompt else 1.0

    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=runner, judge=judge,
                       out_dir=str(out))
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline-haiku"].quality < res.floor   # below the baseline's quality floor
    assert res.winner_id != "baseline-haiku"           # so it is NOT the winner


def test_run_optimize_with_budget_runway(tmp_path):
    baseline = _baseline(tmp_path)
    cands = model_sweep(baseline)
    out = tmp_path / "runs"
    res = run_optimize(baseline, cands, _evalset(), runner=_runner_cost_by_model,
                       judge=lambda p: 1.0, out_dir=str(out), budget_remaining=10.0)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline-haiku"].runway is not None
    assert res.runway_gain is not None and res.runway_gain > 0   # haiku affords more than sonnet
```

> Note: the second test deliberately keeps the judge uniform — per-model quality floors are
> exercised directly in `tests/test_ranking.py` (`select`/`build_result`), which is the right
> layer for that logic. The loop test only needs to confirm wiring + cheapest-wins + artifacts.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.loop'`

- [ ] **Step 3: Write minimal implementation**

`optimize/loop.py`:
```python
"""The optimize loop: eval baseline + candidates (xN) -> rank -> budget pass -> promote."""
import os

from optimize import ranking
from optimize.evalrun import run_evalset


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

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_loop.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 2 skipped)

- [ ] **Step 6: Commit**

```bash
git add optimize/loop.py tests/test_loop.py
git commit -m "feat(optimize): run_optimize orchestrates eval -> rank -> promote"
```

---

### Task 6: the `optimize` CLI subcommand

**Files:**
- Modify: `optimize/cli.py`
- Test: `tests/test_cli_optimize.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_optimize.py`:
```python
import types

import yaml

from optimize import cli
from optimize.ranking import CandidateResult, OptimizeResult


def _evalset_file(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "q", "judge": "ok? 0-1"}]}]}))
    return p


def _config_dir(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return d


def _cr(cid, quality, cost, runway=None):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=1.0, cost_usd=cost,
                           duration_ms=1000.0, repeats=1, cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality, runway=runway)


def _args(tmp_path, **kw):
    base = dict(evalset=str(_evalset_file(tmp_path)), config=str(_config_dir(tmp_path)),
                candidate=None, model_sweep=True, repeats=1, min_quality=None, by="cost",
                out=str(tmp_path / "runs"), promote=None,
                history=str(tmp_path / "no_history"), yes=True,
                budget_remaining=None, budget_config=None, budget_scope="global",
                need_tasks=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cmd_optimize_prints_ranked_table(tmp_path, monkeypatch, capsys):
    canned = OptimizeResult(
        baseline_id="baseline", floor=0.8, winner_id="baseline-haiku", improved=True,
        candidates=[_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)],
        pareto=["baseline-haiku"], cost_delta_pct=-50.0, quality_delta=0.0)
    monkeypatch.setattr(cli, "run_optimize", lambda *a, **k: canned)
    cli.cmd_optimize(_args(tmp_path))
    out = capsys.readouterr().out
    assert "baseline-haiku" in out
    assert "*" in out                  # winner marker
    assert "-50% cost" in out or "-50%" in out


def test_cmd_optimize_with_budget_shows_runway(tmp_path, monkeypatch, capsys):
    canned = OptimizeResult(
        baseline_id="baseline", floor=0.8, winner_id="baseline-haiku", improved=True,
        candidates=[_cr("baseline", 0.8, 0.020, runway=500),
                    _cr("baseline-haiku", 0.8, 0.010, runway=1000)],
        pareto=["baseline-haiku"], cost_delta_pct=-50.0, quality_delta=0.0,
        budget_remaining=10.0, runway_gain=500)
    monkeypatch.setattr(cli, "run_optimize", lambda *a, **k: canned)
    cli.cmd_optimize(_args(tmp_path, budget_remaining=10.0))
    out = capsys.readouterr().out
    assert "runway" in out.lower()
    assert "+500" in out               # runway gain


def test_cmd_optimize_need_tasks_without_budget_errors(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        cli.cmd_optimize(_args(tmp_path, need_tasks=100))


def test_cmd_optimize_rejects_missing_config(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        cli.cmd_optimize(_args(tmp_path, config=str(tmp_path / "nope")))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -v`
Expected: FAIL — `AttributeError: module 'optimize.cli' has no attribute 'cmd_optimize'`

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py`, add these imports near the existing top-of-file imports (after the existing `from optimize.evalrun import run_evalset`):
```python
from optimize.candidates import from_dirs, model_sweep
from optimize.loop import run_optimize
```

Add a print helper + the command (place after `cmd_eval_run`/`cmd_eval_init`):
```python
def _print_optimize_result(result, out_dir):
    by_id = {c.config_id: c for c in result.candidates}
    has_budget = result.budget_remaining is not None
    print("=" * 72)
    print(f"TokenCast - optimize (baseline {result.baseline_id}, "
          f"quality floor {result.floor:.2f})")
    print("=" * 72)
    head = f"   {'config':24}{'quality':>8}{'cost':>11}{'time':>8}{'pass':>6}"
    if has_budget:
        head += f"{'runway':>9}"
    print(head)
    for c in sorted(result.candidates, key=lambda x: x.cost_usd):
        mark = "*" if c.config_id == result.winner_id else (
            "+" if c.config_id in result.pareto else " ")
        line = (f" {mark} {c.config_id[:24]:24}{c.quality:8.2f}{_fmt_cost(c.cost_usd):>11}"
                f"{c.duration_ms / 1000:7.1f}s{c.pass_rate * 100:5.0f}%")
        if has_budget:
            tag = ""
            if result.need_tasks is not None and c.fits is not None:
                tag = " fit" if c.fits else " !fit"
            line += f"{c.runway:>7}t{tag}"
        print(line)
    print()
    note = "" if result.improved else "  [no improvement over baseline]"
    print(f"Winner: {result.winner_id}  ({result.cost_delta_pct:+.0f}% cost, "
          f"{result.quality_delta:+.2f} quality vs baseline){note}")
    if has_budget:
        w = by_id[result.winner_id]
        b = by_id[result.baseline_id]
        print(f"Runway: winner ~{w.runway} tasks vs baseline ~{b.runway}  "
              f"(+{result.runway_gain} tasks, same {_fmt_cost(result.budget_remaining)} budget)")
        if result.need_tasks is not None:
            verdict = "YES" if result.winner_fits else "NO"
            print(f"Need {result.need_tasks} tasks in budget? winner fits: {verdict}")
    print(f"Report:   {os.path.join(out_dir, 'optimize.json')}")
    print(f"Promoted: {os.path.join(out_dir, 'promoted')}")


def cmd_optimize(args):
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

In `main()`, after the existing `eval` subparser block (after `ei.set_defaults(func=cmd_eval_init)`) and before `args = ap.parse_args()`, add:
```python
    o = sub.add_parser("optimize", help="rank candidate configs cost-first; promote a winner")
    o.add_argument("evalset", help="path to an evalset.yaml")
    o.add_argument("--config", required=True, help="baseline config dir")
    o.add_argument("--candidate", action="append", help="extra candidate config dir (repeatable)")
    o.add_argument("--model-sweep", action="store_true", help="add opus/sonnet/haiku variants")
    o.add_argument("--repeats", type=int, default=1, help="eval each config N times (median/p90)")
    o.add_argument("--min-quality", type=float, default=None, help="quality floor (default baseline)")
    o.add_argument("--by", choices=("cost", "time"), default="cost", help="optimize cost or time")
    o.add_argument("--out", default="./runs", help="output dir (logs + optimize.json + promoted/)")
    o.add_argument("--promote", default=None, help="also save the winner config to this dir")
    o.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    o.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    o.add_argument("--budget-remaining", type=float, default=None,
                   help="remaining budget USD -> show runway gained")
    o.add_argument("--budget-config", default=None,
                   help="resolve remaining from a budget JSON instead of --budget-remaining")
    o.add_argument("--budget-scope", default="global", help="budget scope (global | project:NAME)")
    o.add_argument("--need-tasks", type=int, default=None,
                   help="report whether the winner makes N tasks fit the budget")
    o.set_defaults(func=cmd_optimize)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_cli_optimize.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 2 skipped)

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_optimize.py
git commit -m "feat(optimize): tokencast-optimize optimize subcommand (budget-aware)"
```

---

### Task 7: docs + opt-in live smoke

**Files:**
- Modify: `tests/test_live_smoke.py`
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Add an opt-in live smoke test**

Append to `tests/test_live_smoke.py`:
```python
def test_live_optimize(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK optimize smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.candidates import model_sweep
    from optimize.evalset import EvalSet
    from optimize.loop import run_optimize

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\nbudget_usd: 0.10\nmax_turns: 2\n")
    baseline = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [{"name": "file", "weight": 1,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    res = run_optimize(baseline, model_sweep(baseline, models=("haiku",)), evalset,
                       out_dir=str(tmp_path / "runs"), budget_remaining=5.0)
    assert res.winner_id in (c.config_id for c in res.candidates)
    assert res.budget_remaining == 5.0
```

- [ ] **Step 2: Verify the suite (new live test skipped by default)**

Run: `python3.11 -m pytest -q`
Expected: PASS; live tests skipped (e.g. "NN passed, 3 skipped").

- [ ] **Step 3: Update `README.md`** — extend the "Optimizer tier (preview)" section

READ `README.md`. In the "Optimizer tier (preview)" section, after the "Evaluating configs (eval harness)" subsection, append:
```markdown
### Optimizing (cost-first, budget-aware)

Rank candidate configs against an eval set and promote the cheapest that holds quality:

```bash
# try opus/sonnet/haiku variants of the baseline, ranked cost-first under its quality floor
tokencast-optimize optimize evals/generated/evalset.yaml --config configs/baseline/ --model-sweep

# frame the win against a budget: how much more work fits the same cap?
tokencast-optimize optimize evalset.yaml --config configs/baseline/ --model-sweep \
    --budget-remaining 15000 --need-tasks 1000
```

It prints a ranked table (★ winner, + Pareto frontier), the winner-vs-baseline cost delta, and —
when a budget is supplied (`--budget-remaining`, or `--budget-config`/`--budget-scope` to read your
`tokencast_budget.json` ledger) — the **runway gained** ("winner affords ~1,428 tasks vs 830, same
budget") plus a `--need-tasks N` fit verdict. The winner config is written to `runs/promoted/`
(and to `--promote DEST` if given); your live `CLAUDE.md` is never touched.
```

- [ ] **Step 4: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Budget core (sub-project 3a) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Optimize loop (sub-project 3b) — done.** `tokencast-optimize optimize` evals a baseline +
candidate configs (model sweep / supplied dirs), ranks them cost-first under a quality floor
(Pareto surfaced), and promotes a winner. Budget-aware: with a remaining budget it reports
runway gained and a `--need-tasks` fit verdict — the optimize + budget halves, joined.
```

- [ ] **Step 5: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `budget.py` bullet, add:
```markdown
- `optimize/` (loop) — `candidates`/`ranking`/`loop` + `tokencast-optimize optimize`: eval a
  baseline + candidate configs, rank cost-first under a quality floor (Pareto surfaced), promote
  a winner. Budget-aware (imports `budget.runway_tasks`): reports runway gained + `--need-tasks`
  fit when a budget is supplied; fully optional otherwise. Sub-project 3b.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 3 skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document the budget-aware optimize loop; live smoke"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `tokencast-optimize optimize evalset.yaml --config baseline/ --model-sweep` ranks the baseline + model variants cost-first under the baseline's quality floor, marks winner + Pareto, writes `optimize.json` + `promoted/`.
- A cheaper candidate below the quality floor is NOT chosen (ranking tests).
- With `--budget-remaining` (or `--budget-config`/`--budget-scope`), the table shows per-candidate runway and the report states runway gained; `--need-tasks N` reports the winner's fit; `--need-tasks` without a budget errors cleanly.
- With no budget flags, the output is identical to the lean core (optionality).
- `tokencast.py`, `tokencast.html`, and `budget.py` are unchanged; the whole suite runs zero-spend with no SDK installed.
