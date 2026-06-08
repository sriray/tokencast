# TokenCast — project context for Claude Code

This file is the handoff brief. It captures *why* this project exists and the decisions already
made, so you can continue without re-deriving them. Read it before changing things.

## What this is

TokenCast predicts what an **agentic coding task will cost and how long it takes**, so a team can
build software estimates and plan timelines in the metered era. The headline command is `forecast`;
spend attribution (`report`) is a deliberately secondary view.

It started as the companion artifact to an essay, *"Software Just Went COGS"* (lives in the parent
folder, not in this repo). The essay's thesis: the marginal cost of producing software just went
from ~zero to metered, companies are reacting with blunt per-seat/per-task caps, and the missing
discipline is a **pre-flight cost-and-time forecast surfaced in plan mode, before work is approved**.
TokenCast is the one-afternoon proof of that missing half.

## Current state

- `tokencast.py` — Python 3.8+, stdlib only. Subcommands: `forecast` (primary),
  `report`, `estimate`, `demo`, `budget`. Live-pricing refresh via `--refresh-prices`.
  `estimate <plan.md>` (ROADMAP #5) annotates each ticket line of a plan markdown file with a
  p90 cost+time + a sprint total, reusing the `forecast` kNN + accuracy bridge (`parse_plan` +
  `cmd_estimate`; see `docs/superpowers/specs/2026-06-06-estimate-and-packaging-design.md`).
  Installable as a `tokencast` console command (`pipx install tokencast` / `uvx tokencast`).
  Optional `--segment`
  (`--gap-min N`, default 30; `--split-on-user`) on `forecast`/`report` splits a session
  transcript into task-sized units at long idle gaps (ROADMAP #2) — opt-in, backward-compatible,
  pure (`segment_entries`/`parse_session_segments`/`load_segmented`, atop a shared `reduce_entries`).
- `tokencast.html` — single-file, no-build browser version. Parses logs client-side (nothing
  uploaded), auto-refreshes pricing on load, has demo data, cost histogram with a draggable cap,
  and a forecast panel (per-task + sprint, cost + time).
- `README.md` — user-facing docs.
- `optimize/` — NEW heavy tier (depends on `claude-agent-sdk`, `pyyaml`). Wraps a local Claude
  via the Agent SDK to run a task under a config and measure it accurately (`tokencast-optimize
  run`). The light tier (`tokencast.py`) never imports this; the two connect only via JSONL.
  Sub-project 1 of a planned closed-loop optimizer — see `docs/superpowers/specs/` and
  `docs/superpowers/plans/`.
  Sub-project 2 adds the eval harness (`evalset`/`checks`/`sandbox`/`judge`/`scorer`/`evalrun`/
  `generate` + `eval run`/`eval init`): score a config against an eval set (rule checks + LLM
  judge) into a composite quality score with accurate cost/time.
- `budget.py` — NEW optional light-tier module (stdlib-only). `tokencast.py budget` tracks spend
  against a calendar-period cap (global/per-project) across two labeled sources (real usage
  floor + accurate TokenCast runs), reporting consumed/remaining/burn-rate/runway. Fully
  optional: with no `tokencast_budget.json`, nothing else changes. Sub-project 3a; the
  budget-aware optimize loop (3b) imports it for runway framing.
- `optimize/` (loop) — `candidates`/`ranking`/`loop` + `tokencast-optimize optimize`: eval a
  baseline + candidate configs, rank cost-first under a quality floor (Pareto surfaced), promote
  a winner. Budget-aware (imports `budget.runway_tasks`): reports runway gained + `--need-tasks`
  fit when a budget is supplied; fully optional otherwise. Sub-project 3b.
- `optimize/` (generator) — `candidates.generate_candidates` + `optimize --generate N`: a
  failure-driven LLM generator (behind an injectable seam) that reads the baseline's failing
  dimensions and proposes candidate configs mutating instructions + tool selection, evaluated
  and ranked in the same loop. Sub-project 4a; skills/MCP axes are 4b.
- `optimize/` (skills wiring) — `config.skills` (name list → SDK `skills` + `setting_sources`),
  `staging.stage_skills` (a config's `skills/` dir → the run sandbox's `.claude/skills`), and
  `catalog.available_skills`/`available_mcp`. Finishes the deferred skills→SDK wiring so a
  config's skills actually apply. Sub-project 4b-i; the generator's skills/MCP axes are 4b-ii.
- `optimize/` (generator axes) — `generate_candidates` now also mutates the skills + MCP axes
  (names validated against `catalog.available_skills`/`available_mcp`, resolved additively onto
  the baseline) and enriches its prompt with each failing dimension's definition (required dims
  first). `optimize --skills-dir/--mcp-catalog` feed the catalogs. Sub-project 4b-ii.
- `optimize/` (decompose) — `decompose.py` + `tokencast-optimize decompose`: compare a monolithic
  task run vs N LLM-proposed decompositions (ordered sub-tasks run sequentially in one sandbox,
  per-step model routing), scored on the task's dimensions, ranked cost-first under a quality
  floor. Standalone command; injectable decomposer; optimize loop untouched. Sub-project 5.
- `optimize/` (front door) — `auto.py` `run_auto` + `tokencast-optimize auto`: forecast →
  optimize → promote (+ optional `--decompose` on the winner) under one confirmation, writing a
  consolidated `auto.json`. Plus `.claude/skills/tokencast-optimize/SKILL.md`, the conversational
  playbook that drives the CLIs (deterministic math) with LLM judgment (eval drafting, axis choice,
  interpretation). A drift-guard test keeps the skill's command references real. Sub-project 6.
- Both implementations mirror the same math; keep them in sync when you change cost logic.

Run `python tokencast.py demo --out ./sample_logs` then `forecast ./sample_logs --files 8 --tools 30
--count 12` to see everything. Open `tokencast.html` directly in a browser and click "Load demo data".

## Key decisions already made (don't relitigate without reason)

- **Target = Claude Code JSONL logs** at `~/.claude/projects/<project>/<session>.jsonl`. A "task"
  ≈ one session. Features extracted per session: files touched, tool calls, output tokens, turns,
  wall-clock duration.
- **Forecast method**: standardize features, take the k nearest historical sessions (k≈max(5, n/4)),
  report the percentile distribution (p50/p90/p95) of their cost and duration. Sprint totals via
  Monte Carlo over the matched set. Chosen over a fragile regression because it's robust on small
  data and explainable. "Budget the p90" is the consistent guidance.
- **Pricing**: built-in defaults (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5 per 1M; cache write 1.25×,
  read 0.10×) + optional live pull from the community LiteLLM cost map (Anthropic has no official
  machine-readable price feed). Always keep a hardcoded fallback.

## The single most important caveat (and the top roadmap item)

**Claude Code's JSONL `input_tokens` is a streaming placeholder** — 0 or 1 in ~75% of entries,
undercounting raw input by up to ~100x. Cache fields (`cache_creation_input_tokens`,
`cache_read_input_tokens`) are reliable. So TokenCast's absolute costs are a **floor**, and the tool
says so loudly. This is currently framed as a feature (it proves the essay's point: only the provider
has the accurate number). But for the tool to be genuinely useful, the #1 job is to get an accurate
token source. See ROADMAP.md.

**Partially addressed:** the optimizer tier measures runs accurately and stamps a
`tokencast_accurate` marker into its JSONL; `forecast` (CLI + HTML) now prefers those accurate runs
(≥5 with a usable >0-cost signal → calibrated, floor caveat dropped) over the undercounted history,
deduping the pool and labeling the basis. Pure Claude-Code-log forecasts remain a floor until a
provider token API lands.

## Evidence base (verified)

- arXiv 2604.22750, "How Do AI Agents Spend Your Money?" (Microsoft Research + Stanford Digital
  Economy Lab; authors incl. Brynjolfsson, Pentland). Findings we rely on: same task varies up to
  **30x**; agentic tasks ~1000x more tokens than chat; models can't predict their own spend
  (corr ≤0.39) and underestimate; **expert-rated difficulty only weakly tracks actual cost** — the
  empirical case for calibrating estimates on history rather than human sizing.

## Prior art (so we position honestly)

Post-hoc attribution is a solved, crowded space — **ccusage** (4.8k+ stars), several dashboards, and
Anthropic's own **Usage and Cost API**. Do NOT try to out-attribute them. TokenCast's reason to exist
is the **forward-looking** estimate (cost + time, pre-flight, calibrated on your history), which the
rear-view tools and the official API do not do.

## Conventions

- No third-party Python deps. Keep `tokencast.py` stdlib-only unless there's a strong reason.
- Keep the HTML single-file and dependency-free (offline-capable, trust-friendly).
- When you change cost math, update BOTH `tokencast.py` and `tokencast.html` and the README.
- Be honest in all copy about the token-undercount floor until it's actually fixed.
