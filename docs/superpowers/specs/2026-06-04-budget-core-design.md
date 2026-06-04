# TokenCast Budget Core — Design Spec (sub-project 3a of 7)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** the light tier (`tokencast.load`, `tokencast.pct`, `tokencast.money`). No
dependency on the heavy tier or the Agent SDK.

---

## 0. Optionality is a HARD requirement (read first)

Budget is an **entirely optional, additive** feature. This is load-bearing and tested:

- **No budget config present → zero behavior change** anywhere. `forecast`, `report`, `optimize`,
  `eval`, `demo` behave exactly as before. No new *required* flags on any command.
- `budget.py` and `tokencast.py budget` exist but are **inert without a config**: they print a
  friendly "no budget configured" hint and exit 0.
- The budget-aware optimize loop (sub-project 3b) lights up **only** when a budget is explicitly
  supplied; with none, it is byte-for-byte the approved lean loop.
- Budget is **never a precondition** for any other feature. No global state, no implicit
  enforcement, no auto-discovery that changes behavior (config is read only when a budget command
  is run or a budget is explicitly passed).
- The light tier stays **stdlib-only**; budget adds nothing mandatory.

Success criteria (§11) include tests proving the existing commands are unaffected when no budget
file exists.

---

## 1. Why this exists

The metering era is here: per-engineer/per-task spend caps (e.g. Uber's $15k/engineer). An
engineer under a fixed cap needs to (a) see how much of the cap is consumed and how much runway
remains, and (b) reduce per-task cost to extend that runway. TokenCast already has the two halves:
`forecast` ("will this fit?") and `optimize` ("make it cheaper"). The missing connective tissue
is the **budget**: a cap + a spend ledger + **runway** (budget ÷ per-task cost).

This sub-project builds the budget primitive (light tier). Sub-project 3b makes the optimize loop
budget-aware on top of it.

The unifying primitive is **runway**:
```
runway_tasks = remaining_budget / per_task_cost
```
- `optimize` lowers `per_task_cost` → more runway.
- `budget` tracks `remaining_budget` and burn rate → tells you the runway.

---

## 2. Decisions already made (don't relitigate without reason)

- **Tier:** light, **stdlib-only**, offline. New module `budget.py` + a `tokencast.py budget`
  subcommand. Config is **JSON** (no pyyaml). The heavy tier may import `budget.py` (heavy→light).
- **Spend sources = both, clearly separated:** real Claude Code usage (from `~/.claude/projects`,
  carrying the input-token **floor**) AND accurate TokenCast runs (from `runs/`). Displayed as a
  labeled breakdown; `remaining = amount − total`.
- **Scoped budgets:** `global` (engineer) and `project:<name>` (matched on the logs' `project`
  field). A per-task target is supplied at optimize time (3b), not stored here.
- **Time model = calendar period** (monthly/quarterly/annual) anchored at `period_start`, resets
  on the boundary; burn-rate/runway/projected-exhaustion computed against the current window.
- **Optionality** is a hard requirement (§0).

---

## 3. Module layout

```
budget.py            NEW, stdlib-only, light tier: config + ledger + status math
tokencast.py         MODIFY: add a `budget` subcommand that calls into budget.py
```

`budget.py` imports only stdlib + `tokencast` (for `load`, `pct`, `money`). `tokencast.py`'s
`budget` subcommand is the only addition to the light-tier CLI; `forecast`/`report`/`demo` are
untouched. Keeping the logic in `budget.py` (not inline in `tokencast.py`) keeps both files
focused and lets the heavy tier import the primitive without importing the CLI.

> Note: this is a deliberate, scoped change to the light tier. `tokencast.py` stays stdlib-only
> and its existing commands are unchanged (verified by tests).
>
> **Avoid a circular import:** `budget.py` imports `tokencast` at module top; therefore
> `tokencast.py` must import `budget` **lazily, inside the `budget` subcommand handler** (the
> same lazy-import style `tokencast.py` already uses for `random`/`datetime`). By the time the
> handler runs, `tokencast` is fully loaded, so `budget`'s top-level `import tokencast` resolves
> cleanly with no cycle.

---

## 4. Budget config model (`tokencast_budget.json`)

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

- `period` ∈ {`monthly`, `quarterly`, `annual`}.
- `period_start` (ISO date) anchors the cycle; the *current* window is the period containing
  `today`, derived by stepping from the anchor.
- `budgets`: list of `{scope, amount}`. `scope` is `global` or `project:<name>`.
- `BudgetConfig.load(path)` parses + validates with loud, path-prefixed errors (consistent with
  `AgentConfig`): unknown `period`, missing/negative `amount`, malformed `scope`, bad JSON →
  clear `ValueError`. Missing file → `None` (caller prints the "no budget configured" hint).

---

## 5. Spend ledger (both sources, separated)

`budget.py` builds a list of spend records from two sources via the existing `tokencast.load`:
- **real**: `tokencast.load(real_logs_path)` (default `~/.claude/projects`) — tagged `source="real"`.
- **tokencast**: `tokencast.load(runs_path)` (default `./runs`) — tagged `source="tokencast"`.

Each record keeps `{cost, project, ts_first, source}` (already produced by `parse_session`).
`collect_spend(real_logs, runs) -> list[record]` returns the combined, tagged list. Missing/empty
paths contribute nothing (no error).

Filtering:
- `in_period(record, start, end)` — keep records whose `ts_first` date falls in `[start, end)`.
- `in_scope(record, scope)` — `global` matches all; `project:<name>` matches `record.project == name`.

---

## 6. Budget status math (`budget.py`, pure, deterministic via `today` param)

```
current_period(period, anchor_date, today) -> (start_date, end_date)
```
Steps whole periods from `anchor_date` until the window contains `today`.

```
status(budget_config, scope, records, today) -> BudgetStatus
```
- `amount` = the configured amount for `scope` (raise if `scope` not in config).
- window = `current_period(...)`; in-window, in-scope records split by source.
- `consumed_real` = Σ real (a **floor**), `consumed_tokencast` = Σ tokencast,
  `consumed_total` = sum, `remaining` = `amount − consumed_total`.
- `elapsed_days` = `today − start + 1`; `burn_rate_per_day` = `consumed_total / elapsed_days`.
- `runway_days` = `remaining / burn_rate_per_day` (or `inf` if burn 0); `projected_exhaustion` =
  `today + runway_days`, or `None`/"won't exhaust this period" if it lands past `end`.
- `period_start`, `period_end` carried for display.

```
runway_tasks(remaining, per_task_cost) -> int      # floor(remaining / per_task_cost); the loop reuses this
fits(remaining, forecast_cost) -> bool             # forecast_cost <= remaining
```

`BudgetStatus` is a dataclass with the fields above; it never mutates global state.

---

## 7. CLI (`tokencast.py budget` subcommand)

```
python tokencast.py budget
    [--config tokencast_budget.json] [--scope global|project:NAME]
    [--logs ~/.claude/projects] [--runs ./runs]
    [--per-task COST] [--forecast USD]
```
- No config file (at `--config`, default `./tokencast_budget.json`) → print
  `No budget configured. Create tokencast_budget.json to track spend against a cap (see README).`
  and exit 0. (Optionality.)
- With a config: for the chosen `--scope` (default `global`), print the period window, the
  consumed breakdown (real **floor** / tokencast / total), remaining, burn rate, runway (days +
  projected-exhaustion). The real-usage floor caveat is printed.
- `--per-task COST` → also print runway in **tasks** (`runway_tasks`).
- `--forecast USD` → also print whether that sprint **fits** remaining (`fits`).
- `--refresh-prices` reuses the existing light-tier pricing refresh (cost figures honor it).

`forecast`/`report`/`demo` get **no** new behavior; the budget gate stays inside the `budget`
command for v1 (sub-project 3b adds the loop-side runway framing).

---

## 8. Honesty about the floor

Real-usage spend inherits the JSONL input-token undercount: **consumed-real is a floor**, so
`remaining` is an **upper bound** (you may have less runway than shown). The `budget` output says
this explicitly. This is the same caveat `report` already makes, and it reinforces ROADMAP #1
(an accurate token source would make the cap view exact).

---

## 9. Error handling

- Malformed/invalid config → clean `ValueError` with the file path (caught by the CLI → `SystemExit`).
- Missing config file → `None` → friendly hint (not an error).
- Empty/missing log dirs → contribute zero spend (no crash).
- `--scope` not present in the config → clear error listing the configured scopes.

---

## 10. Testing (stdlib, zero spend, offline)

- `current_period`: monthly/quarterly/annual windows from an anchor, for a fixed `today`,
  including a `today` several periods after the anchor.
- `collect_spend` + filters: dual-source tagging, in-period and in-scope filtering
  (global vs project), missing dirs contribute nothing.
- `status`: consumed split (real floor vs tokencast), remaining, burn-rate, runway-days,
  projected-exhaustion (including "won't exhaust this period"), with a fixed `today` and
  synthetic JSONL fixtures.
- `runway_tasks`, `fits`.
- `BudgetConfig.load`: valid config; malformed JSON; unknown period; missing/negative amount;
  missing file → None.
- CLI `budget`: with a tiny synthetic config + logs (assert the breakdown + remaining + runway);
  **no-config path prints the hint and exits 0**.
- **Optionality guard:** a test confirming `forecast`/`report` still run normally with no budget
  file present (and that importing `tokencast`/`optimize` requires nothing from `budget`).

---

## 11. Success criteria

- `python tokencast.py budget` with a `tokencast_budget.json` prints the current-period consumed
  (real-floor / tokencast / total), remaining, burn rate, and runway (days; and tasks with
  `--per-task`), scoped global or per-project, with the floor caveat.
- `--forecast USD` reports whether a sprint fits remaining.
- With **no** budget file, every existing command behaves exactly as before, and `budget` prints a
  friendly hint and exits 0.
- `budget.py` is stdlib-only and importable by the heavy tier (for sub-project 3b's runway framing).
- The whole suite passes offline with zero API spend.

---

## 12. Out of scope (later)

- The budget-aware optimize loop (runway-gained framing, "fit my budget" target) → sub-project 3b
  (a small revision of the already-approved optimize-loop spec to `import budget`).
- Multi-engineer/team rollups, persistent burn-down history files, alerting daemons → future.
- Promotion/enforcement that blocks runs at the cap → deliberately excluded (budget informs; it
  does not police).
