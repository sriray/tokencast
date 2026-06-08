# Changelog

All notable changes to TokenCast are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-06-08

A correctness, robustness, and security hardening pass (eight waves of adversarial
review plus a meta-review and live end-to-end verification). No new features; every
number TokenCast prints is now harder to make wrong. The `tokencast.py` and
`tokencast.html` implementations stay in lockstep on all cost/forecast math.

### Fixed

- **Forecast no longer reports a confident calibrated `$0`.** The accuracy bridge
  now calibrates only on accurate runs that carry a usable (>0) cost signal, so a
  batch of failed/empty harness runs (stamped accurate at `$0`) can't flip the basis
  to "calibrated" and drop the floor caveat. Applied in both `forecast` and
  `estimate`, CLI and browser.
- **Live pricing can't zero out a model family.** A `$0`/negative entry in the
  community cost map (free preview SKUs exist) is skipped instead of overwriting a
  family's price with `$0` and silently zeroing every cost.
- **Forecast weighting uses every neighbor.** The largest neighbor's distance weight
  was ignored, flattening the p90; it now moves the estimate.
- **Parser robustness.** One malformed token value, a non-dict `usage`, a non-string
  `model` id, negative/`inf`/`NaN` token counts, a UTF-8 BOM, fenced code blocks, and
  mixed timezone / naive / numeric timestamps no longer crash, drop a whole session,
  or silently produce `NaN`/`inf` costs.
- **`budget` project scope matches real logs.** `project:<name>` now matches Claude
  Code's mangled-path project dirs (e.g. `-Users-me-dev-tokencast`) instead of
  silently consuming nothing and reporting the full cap as remaining.
- **`budget` no longer double-counts** a run that appears in both the real logs and
  `./runs` (deduped by session, preferring the accurate copy).
- **`estimate` ignores markdown horizontal rules** (`- - -`, `* * *`) instead of
  counting them as plan tickets and inflating the sprint total.
- **`report`/`forecast` validate every numeric CLI input** and bound the Monte-Carlo
  roll-up, so bad flags fail loudly instead of hanging or printing nonsense.

### Security (optimizer tier)

- **Path-traversal closed.** Eval-set task ids and config ids are confined to safe
  path components (an `id` like `../../etc/cron.d/x` can no longer write outside the
  run dir), and rule-check file paths are confined to the task sandbox (no reading
  arbitrary host files to game a score).
- **Symlink handling.** Seed/skills dirs preserve symlinks instead of copying a
  linked secret's contents into persisted run artifacts; the `command`-check trust
  boundary (a rule runs an arbitrary shell command) is now documented loudly.
- **Atomic artifact writes.** Run JSONL and `*.json` summaries are written via a temp
  file + atomic rename, so a crash mid-write can't leave a truncated file that the
  forecaster reads back as a short, under-counted history.

### Changed

- Optimizer ranking excludes failed (`$0`) configs from the Pareto front and the
  winner; the `--max-spend` ceiling halts on measured spend; a non-finite judge score
  maps to `0` instead of a passing `1.0`.

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

[0.3.0]: https://github.com/sriray/tokencast/releases/tag/v0.3.0
