# TokenCast v0.3.0

**Forecast what an agentic coding task will cost and how long it takes — before you approve the work.**

TokenCast is the pre-flight cost-and-time estimate for the metered era of software. Point it at your
Claude Code history and it tells you the p90 cost and wall-clock time of the next task, calibrated on
what your tasks have actually cost — not a human guess. Post-hoc spend attribution is a crowded,
solved space; TokenCast's reason to exist is the **forward-looking** number, surfaced where the
decision gets made.

This is the first packaged release: `pip install tokencast` (no dependencies) gets you the CLI, and
`tokencast.html` is a single, dependency-free file you can open in a browser.

## Highlights

- **`forecast`** — distance-weighted k-NN estimate of a task's cost and time (p50 / p90 / p95) from
  your own session history, a one-word neighbor-fit confidence label (tight / moderate / loose), and
  a Monte-Carlo sprint roll-up (`--count N`). The guidance is consistent: **budget the p90.**
- **`estimate <plan.md>`** — the literal "cost line in the plan": annotates each ticket of a plan
  markdown file with a p90 cost + time and prints a sprint total, reusing the same forecaster.
- **`report`** — past-spend attribution (totals, per-task distribution, by project/model, top tasks,
  a `--cap` overlay) for when you want the rear-view too.
- **`budget`** — optional spend tracking against a calendar-period cap (global / per-project) with a
  dual-source ledger, reporting consumed / remaining / burn-rate / runway. Inert until you opt in.
- **`tokencast.html`** — the whole tool in one file: client-side log parsing (**nothing is
  uploaded**), auto-refreshing pricing, demo data, a cost histogram with a draggable cap, and a
  cost + time forecast panel.
- **Optimizer tier** (`pip install "tokencast[optimize]"`) — `tokencast-optimize` drives Claude via
  the Agent SDK to *measure* a task accurately, score configs against an eval set, run a cost-first
  optimize loop under a quality floor, generate failure-driven candidates, decompose tasks, and chain
  it all through an `auto` front door — plus a `/tokencast-optimize` Claude Code skill.

## Try it in 30 seconds

```sh
pipx install tokencast            # or: uvx tokencast forecast --help
tokencast demo --out ./sample_logs
tokencast forecast ./sample_logs --files 8 --tools 30 --count 12
```

Or open `tokencast.html` in a browser and click **Load demo data**.

## The honest caveat

Claude Code's JSONL `input_tokens` is a streaming placeholder that undercounts raw input, so a
forecast built purely from interactive logs is a **floor**, and the tool says so. The optimizer tier
sidesteps this by measuring runs accurately (real aggregated token counts) and stamping them so
`forecast` can calibrate on real data and drop the floor caveat once it has ≥5 accurate runs. A
provider Usage & Cost API path for pure-log accuracy is the top roadmap item.

## Pricing

Built-in defaults (Opus / Sonnet / Haiku, cache read/write multipliers) plus an optional live pull
from the community LiteLLM cost map (`--refresh-prices`), always with a hardcoded fallback.

---

Full details in [CHANGELOG.md](../CHANGELOG.md). MIT licensed.
