# Changelog

All notable changes to TokenCast are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-06

First packaged release: the light tier is installable as a `tokencast` console
command and the heavy optimizer tier ships as `tokencast-optimize`.

### Added

#### Light tier (`tokencast.py` — stdlib only, Python 3.8+)

- `forecast` — distance-weighted k-nearest-neighbors estimate of a task's cost
  and time (p50 / p90 / p95) from your own session history, with a one-word
  neighbor-fit confidence label (tight / moderate / loose) and a Monte-Carlo
  sprint roll-up via `--count N`.
- `report` — past-spend attribution: totals, per-task distribution, breakdown
  by project and model, top tasks, and a `--cap` overlay.
- `estimate <plan.md>` — plan-mode "cost line in the plan": annotates each
  ticket of a plan/ticket markdown file with a p90 cost + time and prints a
  sprint total, reusing the same forecaster (with optional inline
  `(files=.. tools=..)` hints).
- `budget` — optional spend tracking against a calendar-period cap
  (global / per-project) over a dual-source ledger (real-usage floor +
  accurate TokenCast runs), reporting consumed / remaining / burn-rate /
  runway. Inert with no `tokencast_budget.json`.
- `demo` — synthetic JSONL generator matching Claude Code's schema.
- Task segmentation: opt-in `--segment` (`--gap-min`, `--split-on-user`) splits
  a session transcript into task-sized units at long idle gaps; backward
  compatible.
- Multi-agent reader registry: `--format {auto,claude-code,generic}` with a
  documented portable `generic` JSONL schema, so any agent's logs can be
  forecast once mapped into the shared session-summary shape.
- Stronger forecast model: distance-weighted percentiles + weighted Monte
  Carlo, log-scaled richer features (adds `cache_read`), and an adaptive `k`.
- Accuracy bridge: `forecast` pools history with accurate optimizer runs
  (`./runs`), prefers them when ≥5 exist (calibrating on real token counts and
  dropping the floor caveat), and labels which basis it used.
- Live pricing refresh via `--refresh-prices` (community LiteLLM cost map,
  cached, with a hardcoded fallback).

#### Browser version (`tokencast.html`)

- Single-file, no-build, dependency-free browser tool: client-side log parsing
  (nothing uploaded), auto-refreshing pricing, demo data, a cost histogram with
  a draggable cap, and a cost + time forecast panel. Mirrors the CLI's
  segmentation and accuracy-bridge behavior.

#### Optimizer tier (`optimize/` — extra `[optimize]`: `claude-agent-sdk`, `pyyaml`)

- `tokencast-optimize run` — run one task under one config via the Claude Agent
  SDK and measure it accurately (real token counts, not the JSONL floor),
  stamping a `tokencast_accurate` marker into the JSONL it writes.
- `eval init` / `eval run` — draft a scorable eval set from a repo, then score a
  config against it with deterministic rule checks plus an LLM judge into a
  composite quality score alongside accurate cost / time.
- `optimize` — eval a baseline + candidate configs, rank cost-first under a
  quality floor (Pareto frontier surfaced), and promote a winner; budget-aware
  runway framing and a `--need-tasks` fit verdict.
- Failure-driven candidate generator (`--generate N`) tuning the instructions,
  tools, skills, and MCP-server axes, validated against catalogs.
- `decompose` — compare a monolithic run against LLM-proposed task
  decompositions (per-step model routing), ranked cost-first under a quality
  floor.
- `auto` — one-shot front door chaining forecast → optimize → promote
  (+ optional `--decompose`) behind a single confirmation.
- `doctor` — environment / configuration diagnostics for the optimizer tier.
- `/tokencast-optimize` Claude Code skill — a conversational playbook that
  drives the CLIs for the deterministic math and supplies the LLM judgment.

#### Packaging

- `pyproject.toml` exposes both `tokencast` and `tokencast-optimize` console
  scripts, so the light tier is `pipx install` / `uvx`-runnable with no
  dependencies and the optimizer tier installs via the `[optimize]` extra.
- MIT `LICENSE`.

[0.3.0]: https://github.com/OWNER/tokencast/releases/tag/v0.3.0
