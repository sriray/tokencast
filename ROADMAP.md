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

## 2. Better task segmentation
A session isn't always one task. Explore segmenting by user-turn boundaries, long idle gaps, or
git-commit snapshots in the transcript, so "a task" maps to a unit a planner actually estimates.

## 3. Stronger forecast model
- Add task-type features (test-heavy vs. greenfield vs. refactor; novelty vs. boilerplate).
- Quantile regression or conformal prediction for calibrated intervals (keep kNN as the explainable baseline).
- Per-model curves, and auto re-baseline when a new model id first appears in the logs (cost surface drift).

## 4. Multi-agent support
Add readers for other tools (Cursor, Copilot/`gh`, Codex, Aider) that emit the same per-session summary
shape. The forecast/report layer is agent-agnostic; only the parser differs.

## 5. Packaging & distribution
- `pipx install tokencast` / a single `uvx` entry; publish the repo on GitHub to ship with the essay.
- Host `tokencast.html` (folder picker works the same over https; demo button always works).
- Optional: a `tokencast estimate` mode that takes a plan.md (list of tickets) and annotates each line
  with a p90 cost+time, then prints the sprint total — the literal "cost line in the plan" idea.

## 6. The real ask (north star)
None of this beats the provider shipping it natively. Frame TokenCast as the existence proof: a
**cost-and-time preview inside plan mode**, calibrated on clean cross-customer telemetry, so every
estimate starts from data. If Anthropic/OpenAI build it, TokenCast's job is done.

## Known limitations to keep honest
- Time = wall-clock session duration (includes human think/idle); a directional timeline proxy, not effort.
- Sprint time roll-up is sequential; real schedules parallelize across engineers.
- Pricing feed is community-maintained (LiteLLM), not official; verify high-stakes numbers.
