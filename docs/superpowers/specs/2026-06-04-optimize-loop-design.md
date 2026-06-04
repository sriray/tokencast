# TokenCast Optimize Loop — Design Spec (sub-project 3 of 6)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 1 (harness: `harness.run`, `RunResult`, `AgentConfig`, `pricing`,
`tokencast.pct`) and sub-project 2 (eval harness: `run_evalset`, `EvalReport`, `EvalSet`).
Both merged to `main`.

---

## 1. Why this exists

The eval harness can score one config against an eval set. The optimize loop closes the
cycle: take a **baseline** config and a set of **candidate** configs, evaluate each, **rank**
them, and **recommend/promote** a winner. This is Microsoft Agent Optimizer's core loop
(baseline → candidates → re-eval → rank → deploy), but **cost/time-first**.

This sub-project builds the loop *machinery* + ranking + promote, with two simple candidate
sources. The *smart* LLM-driven candidate generators (instruction/skill/MCP rewrites) are
sub-project 4 and plug into the same seam.

### The two optimization targets (both run through this same loop)

The loop is target-agnostic — it evaluates candidate configs against an eval set and ranks
them. The difference is only what the candidates and eval set are:
- **Target A — optimize a reusable config:** an eval set of representative tasks; candidates
  are config variants; the winner is a config you keep reusing.
- **Target B — optimize how to run one task:** a single-task eval set; candidates are
  approaches to that task; the winner is the cheapest way to hit *its* exit criteria.

### The economic reality (surfaced, not hidden)

Optimizing means evaluating `(1 + n_candidates) × n_tasks × repeats` runs, each of which spends
(dollars or plan credits). For a genuine one-off task this is N× the cost of just doing it — so
the CLI's pre-flight makes the total spend visible and requires confirmation. The wins are real
for recurring work, large tasks, or optimize-on-a-slice (per the sub-project 2 spec).

---

## 2. Decisions already made (don't relitigate without reason)

- **Ranking = cost-first with a quality floor**, Pareto frontier surfaced alongside. Default
  winner = the cheapest candidate whose quality ≥ floor (baseline quality, or `--min-quality`).
- **Candidate sources:** a seam (candidates are just `AgentConfig`s) with two built-ins —
  user-supplied config dirs and a built-in model sweep (opus/sonnet/haiku). Sub-project 4 adds
  richer generators behind the same seam.
- **Variance:** configurable `--repeats N` (default 1). With N>1, rank on aggregates (median
  quality, p90 cost) and report the spread. Pre-flight cost scales by N so the user opts in.
- **Promote:** always write the ranked report + save the winning config to `out_dir/promoted/`;
  an opt-in `--promote DEST` also saves it to a chosen active dir. Never touches the live
  `CLAUDE.md` / `~/.claude`.
- **Injectable seams throughout** (runner, judge passed through `run_evalset`), so the suite
  runs with zero API spend and no SDK installed.
- **Cost always derives from accurate `RunResult` tokens via `pricing`** (never the SDK's own
  number), carried up through `EvalReport`.

---

## 3. Module layout

```
optimize/candidates.py   model_sweep(baseline, models) + from_dirs(paths) -> list[AgentConfig]
optimize/ranking.py      PURE: aggregate repeats -> CandidateResult; quality floor; Pareto;
                         cost-first (or time-first) selection; OptimizeResult + to_json
optimize/loop.py         run_optimize(): eval baseline + candidates (xN) -> rank -> promote
optimize/config.py       MODIFY: add AgentConfig.save(dir)  (serialize to .agent_configs layout)
optimize/cli.py          MODIFY: add the `optimize` subcommand
```

`ranking.py` is pure (no eval, no I/O beyond `to_json`) so the heart of the loop is fully
unit-testable. `optimize/` may import `tokencast`; the light tier never imports `optimize/`.

---

## 4. Candidates (`optimize/candidates.py`)

A candidate is an `AgentConfig` with a distinct `config_id`. Two built-in sources, each
returning `list[AgentConfig]`:

- `model_sweep(baseline, models=("opus", "sonnet", "haiku")) -> list[AgentConfig]`: one variant
  per model via `dataclasses.replace(baseline, model=m, config_id=f"{baseline.config_id}-{m}")`.
  Skips any `m == baseline.model` (the baseline already covers that model, so the variant would
  be a redundant re-eval of the same effective config).
- `from_dirs(paths) -> list[AgentConfig]`: `AgentConfig.load` for each path.

Sub-project 4's generators produce `AgentConfig`s the same way — no separate interface.

### `AgentConfig.save(path)` (added to `config.py`)

Serialize an `AgentConfig` back to a config dir: write `metadata.yaml` (model, budget_usd,
max_turns, config_id), `instructions.md` (if `system_prompt_append`), `tools.json` (if any of
allowed/disallowed/mcp_servers). Round-trips with `AgentConfig.load`. Needed by promote and
by sub-project 4. (Modifying the sub-project-1 `config.py` is in scope here.)

---

## 5. Eval + aggregation (in `loop.py`, types in `ranking.py`)

For each candidate (baseline included), run `run_evalset(evalset, config, runner=, judge=,
out_dir=out_dir/<config_id>/run_<i>)` **`repeats`** times. Aggregate the N `EvalReport`s into a
`CandidateResult` (in `ranking.py`):

```
CandidateResult:
  config_id: str
  quality: float        # median of EvalReport.composite over repeats
  pass_rate: float      # mean of EvalReport.pass_rate over repeats
  cost_usd: float       # p90 (tokencast.pct(.,0.9)) of EvalReport.total_cost_usd over repeats
  duration_ms: float    # p90 of EvalReport.total_duration_ms over repeats
  repeats: int
  cost_min: float       # spread, for honesty
  cost_max: float
  quality_min: float
  quality_max: float
```

With `repeats=1`, median/p90 equal the single value and min==max. `ranking.aggregate(config_id,
reports) -> CandidateResult` is a pure function (list of EvalReports in, CandidateResult out).

---

## 6. Ranking (`optimize/ranking.py`, pure)

- `floor` = `min_quality` if provided, else the baseline candidate's `quality`.
- `eligible` = candidates with `quality >= floor`.
- `select(candidates, baseline_id, floor, by="cost")`:
  - winner = the eligible candidate minimizing `cost_usd` (or `duration_ms` if `by="time"`);
    tie-break by the other metric, then by higher quality, then by config_id (deterministic).
  - if `eligible` is empty → winner = baseline_id, flagged "no candidate met the quality floor".
- `pareto(candidates) -> list[config_id]`: the non-dominated set over (cost_usd, quality). A is
  dominated by B iff `B.cost_usd <= A.cost_usd and B.quality >= A.quality` with at least one
  strict inequality. (Time is reported but the frontier is cost×quality for v1.)

`OptimizeResult`:
```
OptimizeResult:
  baseline_id: str
  floor: float
  winner_id: str
  improved: bool                 # winner != baseline and cheaper at >= floor quality
  candidates: list[CandidateResult]
  pareto: list[str]              # config_ids on the frontier
  # convenience deltas vs baseline, computed for the winner:
  cost_delta_pct: float          # (winner.cost - baseline.cost)/baseline.cost * 100
  quality_delta: float           # winner.quality - baseline.quality
```
`OptimizeResult.to_json(path)` via `dataclasses.asdict`.

---

## 7. The loop + promote (`optimize/loop.py`)

`run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
min_quality=None, by="cost", out_dir="runs", promote_to=None) -> OptimizeResult`:

1. Build the full candidate list: `[baseline] + candidates`, de-duplicated by `config_id`
   (baseline always first/kept).
2. For each: eval `repeats` times via `run_evalset`; `ranking.aggregate` → `CandidateResult`.
   A candidate whose runs all fail still yields a `CandidateResult` (quality 0) — `run_evalset`
   already isolates per-task failures, so a bad candidate can't abort the whole loop.
3. `floor`, `pareto`, `select` → `OptimizeResult` (with deltas). Write `out_dir/optimize.json`.
4. **Promote:** always `AgentConfig.save` the winner's config to `out_dir/promoted/`. If
   `promote_to` is set, also `AgentConfig.save` it there. (The winner's `AgentConfig` is found
   by `config_id` from the candidate list.)
5. Return the `OptimizeResult`.

Fully testable zero-spend: a fake runner whose returned cost varies by `options["model"]` (so
model-sweep produces genuinely different costs and ranking has something real to choose) + a
fake judge.

---

## 8. CLI (`optimize/cli.py` extension)

```
tokencast-optimize optimize EVALSET --config BASELINE_DIR
    [--candidate DIR ...] [--model-sweep] [--repeats N] [--min-quality Q]
    [--by cost|time] [--out DIR] [--promote DEST] [--history PATH] [--yes]
```
- Validate paths (eval set is a file, baseline config is a dir, each `--candidate` is a dir),
  failing cleanly with `SystemExit` (consistent with `eval run`).
- Build candidates = `from_dirs(--candidate)` + (`model_sweep(baseline)` if `--model-sweep`).
- **Pre-flight:** estimate total ≈ `p90 × n_tasks × (1 + n_candidates) × repeats`, printed to
  stderr with honest wording ("per-task p90 × runs, modeled at list prices"); confirm before
  spend unless `--yes`.
- Run `run_optimize`; print a ranked table to stdout — columns: config_id, quality, p90 cost
  (`_fmt_cost`), time, pass-rate, with `*` on the winner and `+` on Pareto members — then the
  winner-vs-baseline delta line and the `optimize.json` / promoted-dir paths.

---

## 9. Safety

- The loop only runs candidate configs through the same isolated `run_evalset` sandbox path
  (sub-project 2) — no new execution surface.
- Promote never mutates the live `CLAUDE.md`/`~/.claude`; it writes config dirs the user
  explicitly points at (`out_dir/promoted/`, or `--promote DEST`).
- Pre-flight makes the multiplied spend (`×(1+candidates)×repeats`) visible and requires
  confirmation.

---

## 10. Testing

- `ranking.py` fully pure-unit-tested: `aggregate` (median quality / p90 cost / spread over
  repeats), `select` (cost-first, time-first, tie-breaks, empty-eligible → baseline),
  `pareto` (domination), delta computation.
- `candidates.py`: `model_sweep` produces correctly-id'd variants; `from_dirs` loads.
- `AgentConfig.save` round-trips through `AgentConfig.load`.
- `loop.run_optimize`: with a fake runner (cost varies by model) + fake judge, assert the
  winner is the cheapest meeting the floor, that `optimize.json` + `promoted/` are written, and
  that a quality floor correctly excludes a cheap-but-bad candidate.
- CLI `optimize`: monkeypatched `run_optimize`, path-validation, ranked-table output, abort path.
- One opt-in live smoke gated by `TOKENCAST_LIVE=1` (baseline + model sweep on a one-task set,
  tiny budget).
- Whole suite runs with zero API spend and no `claude-agent-sdk` installed.

---

## 11. Out of scope (later sub-projects)

- Smart LLM-driven candidate generators (instruction/skill/MCP rewrites) → sub-project 4.
- Task decomposition / multi-model subtask routing → sub-project 5.
- The conversational `/tokencast-optimize` skill front door → sub-project 6.
- Promotion into the live `CLAUDE.md`/`~/.claude` (deliberately excluded for safety).

## 12. Success criteria

- `tokencast-optimize optimize evalset.yaml --config baseline/ --model-sweep` evaluates the
  baseline + model variants, ranks them cost-first under the baseline's quality floor, prints a
  ranked table marking the winner and the Pareto frontier, and writes `optimize.json` + the
  promoted winner config.
- A cheaper candidate that drops below the quality floor is correctly NOT chosen.
- `--repeats N` ranks on median quality / p90 cost and reports the spread.
- `--promote DEST` writes the winner to DEST; without it, the live config is untouched.
- The whole suite passes with zero API spend and no SDK installed.
