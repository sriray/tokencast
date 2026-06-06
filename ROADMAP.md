# TokenCast roadmap

Ordered by leverage. The first item is the one that makes the tool trustworthy; everything else is
secondary until it's done.

## 1. Fix token accuracy (the blocker)

**Status: in progress.** The optimizer tier (`optimize/`, sub-project 1) drives Claude via the
Agent SDK, whose result carries real aggregated token counts — accurate on every run TokenCast
executes. Runs are written back as JSONL so the core `forecast`/`report` calibrate on accurate
data. (The passive path over pre-existing interactive sessions is still a floor.)

The JSONL `input_tokens` placeholder makes absolute costs a floor. Get a real source:
- Prefer **OpenTelemetry metrics** from Claude Code (`claude_code.token.usage` / cost) when enabled.
- Or parse **headless/SDK result JSON** (`--output-format json`) which carries `total_cost_usd` + final usage.
- Or reconcile against the **Usage and Cost API** for ground truth, and use it to *calibrate a correction
  factor* for the cheap JSONL path so the local-only experience stays accurate.
- Acceptance: forecasts within ~±15% of the provider's reported cost on a held-out set.

**Eval harness (sub-project 2) — done.** `optimize/` can now score a config against an eval set
(hybrid: deterministic rule checks + LLM judge), producing a composite quality score alongside
accurate cost/time. This is the precondition for the optimize loop (sub-project 3).

**Budget core (sub-project 3a) — done.** Optional `budget.py` + `tokencast.py budget`: a
calendar-period cap (global / per-project) with a dual-source ledger (real usage floor +
accurate TokenCast runs) reporting consumed/remaining/burn-rate/runway. The optimize loop will
consume this to frame wins as runway gained (sub-project 3b).

**Optimize loop (sub-project 3b) — done.** `tokencast-optimize optimize` evals a baseline +
candidate configs (model sweep / supplied dirs), ranks them cost-first under a quality floor
(Pareto surfaced), and promotes a winner. Budget-aware: with a remaining budget it reports
runway gained and a `--need-tasks` fit verdict — the optimize + budget halves, joined.

**Failure-driven generator (sub-project 4a) — done.** `optimize --generate N` evals the baseline,
feeds its failing dimensions to an LLM that proposes candidate configs (instructions + tool
selection), then evals + ranks them with the rest. Skills & MCP axes are sub-project 4b.

**Skills/MCP wiring (sub-project 4b-i) — done.** A config's `skills` (name allow-list → the SDK
`skills` option, with `setting_sources`) and `skills_source` (dir staged into the run sandbox)
now actually apply; `available_skills`/`available_mcp` catalogs added. The generator's skills/MCP
mutation axes that consume these are sub-project 4b-ii.

**Generator skills/MCP axes (sub-project 4b-ii) — done.** `optimize --generate N` now proposes
skills and MCP servers (by name, validated against the `--skills-dir`/`--mcp-catalog` catalogs and
added onto the baseline), prompted with each failing dimension's definition. Task decomposition is
sub-project 5.

**Task decomposition (sub-project 5) — done.** `optimize decompose` compares a monolithic task run
against N LLM-proposed decompositions (sub-tasks run sequentially in one sandbox, each optionally
on a cheaper model) and reports the cheapest strategy meeting a quality floor. The `/tokencast-
optimize` skill front door is sub-project 6.

**Front door (sub-project 6) — done.** `tokencast-optimize auto` chains forecast → optimize →
promote (+ optional `--decompose`) under one confirmation, and a `/tokencast-optimize` Claude Code
skill drives the whole machine conversationally — code for the deterministic parts (forecast,
scoring, ranking), the LLM for judgment (eval-set drafting, axis choice, interpretation).

**Accuracy bridge — done (light tier).** The harness stamps a `tokencast_accurate` marker into the
JSONL it writes; `forecast` now pools history + `./runs`, prefers the accurate runs when ≥5 exist
(calibrating on real token counts and dropping the floor caveat), and labels which basis it used —
in both `tokencast.py` and `tokencast.html`. The remaining accuracy path is the provider Usage &
Cost API for pure Claude-Code-log forecasts.

## 2. Better task segmentation

**Status: done (idle-gap heuristic).** A session isn't always one task. `forecast`/`report` now take
an opt-in `--segment` flag that splits each transcript into task-sized units wherever the wall-clock
gap between consecutive entries exceeds `--gap-min` (default 30 min), with an optional
`--split-on-user` to also cut at fresh user turns between tasks — so "a task" maps to a unit a planner
actually estimates. Default behavior (one file = one session) is unchanged; the split is pure,
stdlib-only, and mirrored in `tokencast.html`. Segment costs/features sum to the session totals; the
unit changes, not the token accuracy (see #1). Git-commit-snapshot segmentation is deferred (commits
aren't reliably present in the transcript). See `docs/superpowers/specs/2026-06-06-task-segmentation-design.md`.

## 3. Stronger forecast model

**Status: improved (distance-weighted kNN).** `forecast` now uses distance-weighted percentiles +
weighted Monte-Carlo (closer past tasks count more, reducing to the old unweighted result when
distances are equal), log-scaled richer features (adds `cache_read`, the real cost driver), an
adaptive `k` that's saner on tiny pools, and a one-word neighbor-spread confidence label
(tight/moderate/loose). kNN stays the explainable baseline; default-on, output shape preserved;
mirrored in `tokencast.html`. See `docs/superpowers/specs/2026-06-06-forecast-model-design.md`.

Still open:
- Add task-type features (test-heavy vs. greenfield vs. refactor; novelty vs. boilerplate).
- Quantile regression or conformal prediction for calibrated intervals (keep kNN as the explainable baseline).
- Per-model curves, and auto re-baseline when a new model id first appears in the logs (cost surface drift).

## 4. Multi-agent support

**Status: done (reader registry + generic schema).** The forecast/report layer is agent-agnostic;
only the parser differs. `tokencast.py` now has a `READERS` registry (`claude-code` == today's
`parse_session`, plus a portable `generic` JSONL reader), a per-file sniffer, and a `--format
{auto,claude-code,generic}` flag on `forecast`/`report` (default `auto`: detect Claude Code, fall
back gracefully; a directory can mix tools). Both readers reduce to the identical session-summary
shape, so cost/forecast math is untouched and the input-token undercount honesty carries over.
Default behavior (no `--format`) is byte-for-byte unchanged. Native Cursor/Copilot/Codex/Aider
readers are future work that slot into the same registry (or pre-process to the generic schema);
the HTML mirror stays Claude-Code-focused for now. See
`docs/superpowers/specs/2026-06-06-multi-agent-readers-design.md`.

## 5. Packaging & distribution

**Status: done (in-repo pieces).** `pyproject.toml` now exposes a `tokencast` console entry (next to
`tokencast-optimize`), so the light tier is `pipx install`/`uvx`-runnable. And `tokencast estimate
<plan.md>` reads a plan/ticket markdown file, annotates each ticket with a p90 cost+time (reusing the
exact forecaster + accuracy bridge; optional inline `(files=.. tools=..)` hints), and prints the
sprint total — the literal "cost line in the plan." See
`docs/superpowers/specs/2026-06-06-estimate-and-packaging-design.md`.

Remaining (external, manual — need network/credentials a human holds):
- Publish to PyPI (`python -m build` + `twine upload`) and verify `pipx install tokencast` / `uvx
  tokencast forecast …` from a clean machine; bump the `pyproject.toml` version first.
- Push the repo public on GitHub to ship alongside the essay; tag a release.
- Host `tokencast.html` over HTTPS (any static host / GitHub Pages); the folder picker works the same
  over https and the demo button always works.

## 6. The real ask (north star)
None of this beats the provider shipping it natively. Frame TokenCast as the existence proof: a
**cost-and-time preview inside plan mode**, calibrated on clean cross-customer telemetry, so every
estimate starts from data. If Anthropic/OpenAI build it, TokenCast's job is done.

## Known limitations to keep honest
- Time = wall-clock session duration (includes human think/idle); a directional timeline proxy, not effort.
- Sprint time roll-up is sequential; real schedules parallelize across engineers.
- Pricing feed is community-maintained (LiteLLM), not official; verify high-stakes numbers.
