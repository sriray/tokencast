# estimate + packaging — design (ROADMAP #5)

Status: implemented. Light-tier, stdlib-only.

## Why

ROADMAP #5 ("Packaging & distribution") has two buildable pieces that don't need network or
credentials:

1. **`tokencast estimate plan.md`** — the literal "cost line in the plan" idea. Take a
   plan/ticket markdown file, annotate each ticket line with a p90 cost + p90 time, and print a
   sprint total. This is the highest-value piece: it puts a calibrated forecast directly next to
   the work being scoped, which is exactly the pre-flight discipline the essay argues for.
2. **Packaging readiness** — expose the light tier as a runnable console command so
   `pipx install tokencast` / `uvx tokencast` work. Today `pyproject.toml` only ships
   `tokencast-optimize`.

Actually publishing to PyPI/GitHub and hosting `tokencast.html` are external manual steps (no
network/credentials here); they're documented as follow-ups below, not performed.

## CLI shape

```
tokencast estimate <plan.md> [path] \
    [--runs ./runs] [--files N] [--tools N] [--output N] \
    [--segment] [--gap-min N] [--split-on-user] [--refresh-prices]
```

- `<plan.md>` (required, positional): the plan/ticket markdown file to annotate.
- `[path]` (optional positional, default `~/.claude/projects`): the history root, same as
  `forecast`.
- `--runs` (default `./runs`): accurate-run pool, same as `forecast`.
- `--files/--tools/--output`: GLOBAL default size hints applied to any ticket that doesn't carry
  its own inline hint. As with `forecast`, an unset feature falls back to the history mean.
- `--segment/--gap-min/--split-on-user`: passed straight through to the same segmented loader the
  `forecast` command uses, so the calibration pool is built identically.

## Plan-parsing rule (documented, simple, pure)

`parse_plan(text) -> [Ticket]` where each `Ticket` is a small namedtuple
`(text, files, tools, output)`. A line is a **ticket** iff, after stripping leading whitespace, it
begins with one of:

- a bullet marker: `- `, `* `, or `+ ` (including GitHub checkboxes `- [ ] ` / `- [x] ` — the
  checkbox token is stripped from the ticket text);
- an ordered-list marker: `1.`, `2)`, etc. (`^\d+[.)]\s`).

Everything else is ignored: blank lines, ATX headings (`#`), block quotes, fenced code, and prose
paragraphs. The marker and any checkbox are stripped; the remaining text (trimmed) is the ticket
label. Empty-after-strip lines are dropped.

### Inline size hint (optional, documented)

A ticket may hint its own size with a trailing parenthesised group of `key=value` tokens, e.g.

```
- Add OAuth login flow (files=8 tools=30)
- Refactor the parser (tools=50 output=4000)
```

Recognised keys: `files`, `tools`, `output` (integers). The hint group is parsed out and
**removed from the displayed ticket text**, and the parsed values override the global
`--files/--tools/--output` for that ticket only. Unrecognised keys / malformed groups are left in
the text untouched (treated as ordinary prose), so this never corrupts a normal ticket.

## Forecast reuse (no new model, honours the accuracy bridge)

`cmd_estimate` builds its calibration pool with the **exact same** code path as `cmd_forecast`:
`load`/`load_segmented` over `path` + `runs`, `_dedup_sessions`, then prefer the accurate subset
(`accurate` flag) when `>= 5` exist, else the full pool (a FLOOR). It reuses `_knn_forecast`,
`_weighted_pct`, and `_forecast_features` WITHOUT changing their signatures.

Per ticket: the target feature dict is `{files_touched, tool_calls, output}` from the ticket's
inline hint if present, else the global `--files/--tools/--output`, else (per feature) the history
mean — identical fallback to `forecast`. `_knn_forecast(sessions, target)` returns the matched
neighbours + weights; the ticket's p90 cost = `_weighted_pct(ncosts, cweights, .9)` and p90 time =
`_weighted_pct(ndurs, dweights, .9)` (n/a when no neighbour carries a duration).

### Labeling (carries the floor/calibrated spirit)

The header states the basis exactly like `forecast`:

- `>= 5` accurate runs → "Calibrated on N accurate harness runs (real token counts)."
- otherwise → "Built on Claude Code logs that undercount input tokens -- these are a FLOOR."

The per-ticket and sprint guidance keeps the "budget the p90" line.

## Output

```
====================================================================
TokenCast - plan estimate (p90 cost + time per ticket)
====================================================================
Basis: <calibrated | FLOOR line>
Matched each ticket against its k nearest past tasks (of N).

  p90 $X.XX   p90 NN min   <ticket text>
  ...

Sprint total (sum of per-ticket p90s): $Y.YY   ~ZZ min  (sequential)
Monte-Carlo total p50/p90 over the matched draws: ...
  -> Budget and schedule to the p90, not the p50.
```

The sprint total is reported two ways: the **sum of per-ticket p90s** (simple, conservative,
matches the "cost line in the plan" mental model) and a **Monte-Carlo** p50/p90 over the union of
matched neighbour draws (consistent with `forecast`'s sprint roll-up). Both are sequential-time
roll-ups; the same "parallelize to compress" caveat applies.

## Packaging

`pyproject.toml` gains, under `[project.scripts]`:

```
tokencast = "tokencast:main"
```

alongside the existing `tokencast-optimize = "optimize.cli:main"`. `tokencast.py` already declares
`py-modules = ["tokencast"]` and has a `main()` entry, so `pipx install .` / `uvx --from . tokencast`
resolve the light tier with zero third-party deps.

## What was touched in tokencast.py (for the concurrent reader refactor)

- ADDED (new, pure): `parse_plan`, `_PlanTicket` namedtuple, `_parse_size_hint` helper.
- ADDED (new): `cmd_estimate`, plus small `_estimate_pool` helper that factors the
  pool-build/accurate-preference shared with `cmd_forecast` (cmd_forecast itself is NOT modified —
  the helper is additive and only called by cmd_estimate to avoid signature churn).
- TOUCHED in `main()`: added the `estimate` subparser block only. No existing subparser changed.
- NO reused-helper signature changed (`load`, `load_segmented`, `_dedup_sessions`,
  `_knn_forecast`, `_weighted_pct`, `_forecast_features` all untouched).

## Manual follow-ups (external; cannot be done here)

1. **PyPI publish**: `python -m build` then `twine upload dist/*` with a PyPI token. Verify
   `pipx install tokencast` and `uvx tokencast forecast ...` from a clean machine.
2. **GitHub publish**: push the repo public to ship alongside the essay; tag a release matching
   `pyproject.toml` version.
3. **Host `tokencast.html`**: serve the single file over HTTPS (any static host / GitHub Pages).
   The folder picker works the same over https; the demo button always works offline.

## Tests

`tests/test_estimate.py`: `parse_plan` extracts bullet/checkbox/ordered tickets and ignores
headings/blanks/prose; inline hint overrides the global target; `cmd_estimate` on demo history
prints a p90 line per ticket + a sprint total; floor-vs-accurate labeling carries through;
`pyproject.toml` declares the `tokencast` script entry.
