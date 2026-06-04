# TokenCast

Predict what an agentic coding task will **cost** and how **long** it takes — so you can build
software estimates and plan timelines in the metered era. ~300 lines, no dependencies, Python 3.8+
(or open the no-install browser version).

Estimation used to be a human sizing a ticket. That no longer predicts the bill: a
[Microsoft/Stanford study](https://arxiv.org/abs/2604.22750) found human-rated difficulty only
weakly tracks actual token cost, and the *same task can vary up to 30x*. So TokenCast doesn't ask
you to guess. It reads the session logs your agent already writes to disk, learns what tasks *like
the one you're planning* have actually cost and taken, and returns a **range** (p50 / p90 / p95)
for a single task or a whole sprint.

This is a deliberately small provocation, not a product. It's the companion to the essay
*"Software Just Went COGS."* The point isn't the parser. The point is that a solo
afternoon gets you a usable forecast off logs that are *known to be wrong* — so imagine
what Anthropic or OpenAI could ship as a "cost preview" inside plan mode, sitting on
clean, cross-customer telemetry. They're the only ones who can do it accurately. They should.

## How this differs from existing tools

Telling you what you *already spent* is a solved, crowded problem — [ccusage](https://ccusage.com/),
several dashboards, and Anthropic's own [Usage and Cost API](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api)
all do post-hoc attribution, most of them better than this. TokenCast deliberately does **not**
try to compete there. It does the **forward-looking** half almost nobody does: turn your history
into a *pre-flight* estimate of what a task will cost *before* you run it. And that problem is
genuinely hard — a [Microsoft Research / Stanford study](https://arxiv.org/abs/2604.22750) found the
same agent on the same task can vary up to **30x**. So treat the forecast as a calibrated bet, not a
quote, and budget the p90.

## Easiest: no install (browser)

Open **`tokencast.html`** by double-clicking it. No Python, no terminal, no setup.
Click **Load demo data** to try it instantly, or **Choose your ~/.claude/projects folder**
to run it on your real history. Everything is parsed in the browser — nothing is uploaded.
Works on any OS.

## Power user: the CLI

```bash
# 1. Generate synthetic logs so you can try it with no setup
python tokencast.py demo --out ./sample_logs

# 2. Estimate one task (cost + time) from your history
python tokencast.py forecast ./sample_logs --files 8 --tools 30

# 3. Roll a sprint of 12 similar tasks into a budget + timeline
python tokencast.py forecast ./sample_logs --files 8 --tools 30 --count 12

# 4. (secondary) Attribute past spend, and see what a cap would clip
python tokencast.py report ./sample_logs --cap 5
```

Point it at your real Claude Code logs by passing `~/.claude/projects` (the default):

```bash
python tokencast.py report
python tokencast.py forecast --files 12 --tools 40
```

## What it does

- **forecast** (the point) — finds the *k* most similar past tasks by feature vector
  (files touched, tool calls, output tokens, turns) and returns a **cost and time** estimate
  as a range. Pass `--count N` to roll up a sprint/project total via Monte Carlo. This is the
  number you put in your estimate — calibrated on your history, not a human guess.
- **report** (secondary) — total spend, per-task distribution (p50/p90/p95/max), breakdown by
  project and model, your most expensive tasks, and an optional `--cap` overlay showing how many
  tasks a hard ceiling would have cut off mid-work. Tools like ccusage already do this well.
- **demo** — writes synthetic JSONL matching Claude Code's schema so you can try
  everything without real data.

Time estimates use wall-clock session duration (first-to-last event), so they include human
think/idle time — treat them as a rough timeline proxy, not billed compute.

## The honest part (and the whole argument)

Claude Code's JSONL logs are **known to undercount input tokens** — `input_tokens` is a
streaming placeholder that's 0 or 1 in ~75% of entries, while the real volume lives in the
cache fields (which *are* reliable). So TokenCast's absolute numbers are a **floor**, and
the tool flags this loudly in its output.

That gap is the thesis. The accurate number exists; only the provider has it. A vendor-neutral
"FinOps for agentic engineering" layer needs the labs to expose per-step token telemetry — or
the labs should just ship the cost preview themselves.

## Pricing (and staying current)

Anthropic publishes **no machine-readable price feed**, so TokenCast ships with built-in
defaults (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5 per 1M tokens; cache write 1.25×, read 0.10×)
and can pull live rates from the community [LiteLLM cost map](https://github.com/BerriAI/litellm),
which is day-0 updated.

- **Browser:** prices auto-refresh from the feed on load (silent fallback to defaults if offline);
  there's also a manual **↻ Refresh from live pricing** button. Any field is editable.
- **CLI:** add `--refresh-prices` to fetch and cache the latest (`~/.tokencast_prices.json`);
  it falls back to the cache, then to built-in defaults, if the network is unavailable.

```bash
python tokencast.py report --refresh-prices
```

**Verify high-stakes numbers against [Anthropic's pricing page](https://platform.claude.com/docs/en/about-claude/pricing).**
The feed is community-maintained, not official — which is itself part of the argument: even *prices*
should be a queryable endpoint the labs publish.

## Extending it

The parser targets Claude Code today. Any agent that logs per-message token usage
(model + input/output/cache tokens) can be supported by adding a reader that emits the same
session summary shape. PRs welcome in spirit; this is a sketch meant to be forked.

## Optimizer tier (preview)

The `forecast`/`report`/`demo` commands above are the stdlib-only, offline core. A separate
**optimizer tier** wraps a locally-running Claude via the Claude Agent SDK to *measure runs
accurately* — the SDK returns real token counts, so this path is not subject to the JSONL
undercount floor that the core warns about.

Install the extra and run one task under one config:

```bash
pip install -e ".[optimize]"        # pulls claude-agent-sdk + pyyaml
tokencast-optimize run examples/task.md --config configs/baseline/ --budget 2.00 --out runs/
python tokencast.py report runs/    # the core reads the accurate logs back
```

A config dir mirrors Agent Optimizer's layout: `metadata.yaml` (model, budget, max_turns),
`instructions.md` (system-prompt append), `tools.json` (allowed/disallowed tools, MCP servers),
and an optional `skills/` dir. Before any real spend the CLI prints a pre-flight cost estimate
and asks you to confirm.

This is sub-project 1 of a planned closed-loop optimizer (eval harness, candidate generation,
model/skill/MCP optimization, task decomposition). See `docs/superpowers/specs/`.
