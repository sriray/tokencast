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

## Budgets (optional)

In the metered era teams set hard spend caps (e.g. a per-engineer quarterly budget). TokenCast
can track spend against a cap and tell you your **runway** — entirely optional, stdlib-only,
offline. Create a `tokencast_budget.json`:

```json
{
  "period": "quarterly",
  "period_start": "2026-04-01",
  "budgets": [
    { "scope": "global", "amount": 15000 },
    { "scope": "project:tokencast", "amount": 2000 }
  ]
}
```

```bash
python tokencast.py budget                       # consumed / remaining / burn rate / runway
python tokencast.py budget --scope project:tokencast --per-task 12.50
python tokencast.py budget --forecast 800        # would an $800 sprint fit what's left?
```

It counts two spend streams, labeled separately: your real Claude Code usage
(`~/.claude/projects`, a **floor** due to the input-token undercount) and accurate TokenCast
runs (`./runs`). With no `tokencast_budget.json`, nothing changes and `budget` just prints a hint.

## Optimizer tier (preview)

The `forecast`/`report`/`demo` commands above are the stdlib-only, offline core. A separate
**optimizer tier** wraps a locally-running Claude via the Claude Agent SDK to *measure runs
accurately* — the SDK returns real token counts, so this path is not subject to the JSONL
undercount floor that the core warns about.

### The whole loop, end to end

The recommended entry is the **`/tokencast-optimize` skill** — describe your task, project, and
budget, and it drives everything below: it does the judgment (drafting the eval set, choosing what
to optimize, reading the numbers) and shells out to these CLIs for the deterministic math. Under
the hood it's a handful of composable steps, each usable on its own and each gating real spend
behind a confirmation:

| Step | Command | What it does |
| --- | --- | --- |
| Forecast | `python tokencast.py forecast` | pre-flight cost/time from your history |
| Draft an eval set | `tokencast-optimize eval init` | exit criteria → scorable dimensions |
| Score a config | `tokencast-optimize eval run` | composite quality + accurate cost/time |
| Optimize (+ decompose) | `tokencast-optimize auto … --decompose` | rank configs across every axis, promote the winner, compare task splits |
| Track budget | `python tokencast.py budget` | spend vs cap → runway |

The axes `auto` optimizes: **model**, **instructions**, **tools**, **skills**, **MCP servers**, and
**task decomposition** — cost-first under a quality floor, budget-aware, all optional.

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

`run` is the foundation; the full closed-loop optimizer is built on top of it — eval harness,
cost-first ranking, failure-driven candidate generation, skills/MCP axes, and task decomposition,
tied together by the `auto` command and the `/tokencast-optimize` skill (below). See
`docs/superpowers/specs/` for the design of each piece.

### Evaluating configs (eval harness)

Score a config against an eval set (one or many tasks). Each task runs in an isolated sandbox
(fresh temp dir, optionally seeded from a dir or a git worktree), then is scored by deterministic
rule checks (tests pass, file exists) plus an LLM judge for qualitative dimensions:

```bash
# draft an eval set from your repo (LLM; review before running)
tokencast-optimize eval init --root . --out evals/generated/

# run an eval set under a config, scored
tokencast-optimize eval run evals/generated/evalset.yaml --config configs/baseline/ --out runs/
```

`eval run` prints a composite quality score, pass rate, and total cost/time — the numbers the
optimize loop (next sub-project) ranks candidate configs on. Generated eval sets are drafts:
their rule checks are LLM-authored shell commands, so review them before running.

### Optimizing (cost-first, budget-aware)

Rank candidate configs against an eval set and promote the cheapest that holds quality:

```bash
# try opus/sonnet/haiku variants of the baseline, ranked cost-first under its quality floor
tokencast-optimize optimize evals/generated/evalset.yaml --config configs/baseline/ --model-sweep

# frame the win against a budget: how much more work fits the same cap?
tokencast-optimize optimize evalset.yaml --config configs/baseline/ --model-sweep \
    --budget-remaining 15000 --need-tasks 1000
```

It prints a ranked table (* winner, + Pareto frontier), the winner-vs-baseline cost delta, and —
when a budget is supplied (`--budget-remaining`, or `--budget-config`/`--budget-scope` to read your
`tokencast_budget.json` ledger) — the **runway gained** ("winner affords ~1,428 tasks vs 830, same
budget") plus a `--need-tasks N` fit verdict. The winner config is written to `runs/promoted/`
(and to `--promote DEST` if given); your live `CLAUDE.md` is never touched.

Add `--generate N` to have an LLM read the baseline's eval failures and propose N candidate
configs (rewritten instructions / adjusted tool lists) — they're evaluated and ranked alongside
the rest:

```bash
tokencast-optimize optimize evalset.yaml --config configs/baseline/ --generate 3
```

The generator only ever changes instructions and tool allow/deny lists (the skills & MCP axes
come later); its proposals run through the same sandbox + ranking as any candidate.

A config can also declare which **skills** it uses (a `skills:` name list in `metadata.yaml`,
selecting from your `~/.claude/skills` + plugins) and bring its own skills via a `skills/`
subdir (staged into each run's sandbox). MCP servers are set via `tools.json`'s `mcp_servers`.
These become optimization axes the generator can tune in a later step.

With `--generate N`, the generator now tunes those axes too: it's shown each failing dimension's
definition (rubric / rule) and the skills + MCP servers you have available, and may propose adding
some to a candidate. Point it at a specific catalog with `--skills-dir DIR` (default
`~/.claude/skills`) and `--mcp-catalog FILE` (default `~/.claude.json`); proposed names are
validated against those catalogs and added on top of the baseline's.

### Decomposing a task

Sometimes the cheapest win isn't a different config — it's splitting the task into smaller
sub-tasks (and routing the easy ones to a cheaper model). `decompose` compares the whole-task run
against LLM-proposed decompositions and reports which is actually cheaper at an acceptable quality:

```bash
tokencast-optimize decompose evalset.yaml --config configs/baseline/ --generate 3
```

Each decomposition's sub-tasks run in sequence in one sandbox (later steps see earlier file
changes) and are scored on the same dimensions; per-task it reports the monolithic vs the winning
decomposition's cost/time. Use `--by time` to optimize wall-clock and `--min-quality` to set the
floor (default: the monolithic run's quality).

### One-shot front door

`auto` chains the common path — forecast, optimize (model + instructions + tools + skills + MCP),
promote the winner — behind a single confirmation, and optionally compares task decompositions too:

```bash
tokencast-optimize auto evalset.yaml --config configs/baseline/ --generate 3 --decompose
```

`--generate N` is the optimize axis (candidate configs); `--decompose` proposes its own
decompositions on the winner, `--decompose-generate N` per task (default 2), so `--decompose`
always tries real splits even without `--generate`. It writes a consolidated `auto.json`. For a
conversational driver that drafts the eval set, forecasts, and interprets the results for you, use
the `/tokencast-optimize` skill — it calls these
CLIs for the deterministic work (forecast, scoring, ranking) and handles the judgment (what to
optimize, how to read the numbers) itself.
