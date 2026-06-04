# TokenCast Budget Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, stdlib-only budget primitive — a calendar-period cap (global or per-project) plus a dual-source spend ledger (real Claude Code usage, floored, + accurate TokenCast runs) that computes consumed/remaining/burn-rate/runway — exposed via `python tokencast.py budget`.

**Architecture:** A new light-tier module `budget.py` (stdlib only) holds the config, ledger, and pure status math; `tokencast.py` gains a `budget` subcommand that calls into it (imported lazily to avoid a circular import). Everything is additive and optional: with no budget file, nothing changes and `budget` prints a hint.

**Tech Stack:** Python 3.8+ stdlib only (`json`, `datetime`, `os`, `dataclasses`). Reuses `tokencast.load`/`tokencast.pct`/`tokencast.money`. No `claude-agent-sdk`, no `pyyaml`, no `optimize/`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-budget-core-design.md`

**Environment note:** the working interpreter is **`python3.11`** (`python`/`python3` lack pytest). Use `python3.11 -m pytest ...`.

**Commit note:** end each commit message with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

**Optionality is a hard requirement:** with no `tokencast_budget.json`, every existing command behaves exactly as before. Task 5 includes a test guarding this.

---

## File Structure

- Create: `budget.py` — stdlib-only: `BudgetEntry`/`BudgetConfig` (load+validate), period math, `SpendRecord`/`collect_spend`, `status` + `runway_tasks`/`fits`.
- Modify: `tokencast.py` — add `cmd_budget` + the `budget` subparser (only addition; existing commands untouched).
- Create: `tests/test_budget.py`.
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.html`, everything under `optimize/`.

---

### Task 1: budget config (BudgetEntry + BudgetConfig.load)

**Files:**
- Create: `budget.py`
- Test: `tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

`tests/test_budget.py`:
```python
import datetime
import json

import pytest

from budget import BudgetConfig


def _write(tmp_path, data):
    p = tmp_path / "tokencast_budget.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_load_valid_config(tmp_path):
    path = _write(tmp_path, {
        "period": "quarterly", "period_start": "2026-04-01",
        "budgets": [{"scope": "global", "amount": 15000},
                    {"scope": "project:tokencast", "amount": 2000}]})
    cfg = BudgetConfig.load(path)
    assert cfg.period == "quarterly"
    assert cfg.period_start == datetime.date(2026, 4, 1)
    assert cfg.amount_for("global") == 15000.0
    assert cfg.amount_for("project:tokencast") == 2000.0


def test_load_missing_file_returns_none(tmp_path):
    assert BudgetConfig.load(str(tmp_path / "nope.json")) is None


def test_load_rejects_bad_period(tmp_path):
    path = _write(tmp_path, {"period": "weekly", "period_start": "2026-04-01",
                             "budgets": [{"scope": "global", "amount": 1}]})
    with pytest.raises(ValueError, match="period"):
        BudgetConfig.load(path)


def test_load_rejects_bad_date(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "nope",
                             "budgets": [{"scope": "global", "amount": 1}]})
    with pytest.raises(ValueError, match="period_start"):
        BudgetConfig.load(path)


def test_load_rejects_bad_scope_and_amount(tmp_path):
    p1 = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                           "budgets": [{"scope": "team", "amount": 1}]})
    with pytest.raises(ValueError, match="scope"):
        BudgetConfig.load(p1)
    p2 = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                           "budgets": [{"scope": "global", "amount": -5}]})
    with pytest.raises(ValueError, match="amount"):
        BudgetConfig.load(p2)


def test_amount_for_unknown_scope_raises(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                             "budgets": [{"scope": "global", "amount": 1}]})
    cfg = BudgetConfig.load(path)
    with pytest.raises(ValueError, match="not in budget config"):
        cfg.amount_for("project:ghost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'budget'`

- [ ] **Step 3: Write minimal implementation**

`budget.py`:
```python
"""Optional budget tracking for TokenCast (light tier, stdlib-only, offline).

A budget = a calendar-period cap (global or per-project) + a spend ledger drawn from two
sources: real Claude Code usage (~/.claude/projects, a FLOOR due to the input-token undercount)
and accurate TokenCast runs (./runs). Computes consumed/remaining/burn-rate/runway.

Entirely optional: nothing else in TokenCast requires a budget. With no config, the `budget`
command prints a hint and exits.
"""
import datetime
import json
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import tokencast

_PERIODS = ("monthly", "quarterly", "annual")


@dataclass
class BudgetEntry:
    scope: str          # "global" or "project:<name>"
    amount: float


@dataclass
class BudgetConfig:
    period: str
    period_start: datetime.date
    budgets: List[BudgetEntry]

    @classmethod
    def load(cls, path):
        """Return a BudgetConfig, or None if the file does not exist."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"failed to parse {path}: {e}")
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object")
        period = data.get("period")
        if period not in _PERIODS:
            raise ValueError(f"{path}: period must be one of {_PERIODS}, got {period!r}")
        ps = data.get("period_start")
        try:
            period_start = datetime.date.fromisoformat(ps)
        except (TypeError, ValueError):
            raise ValueError(
                f"{path}: period_start must be an ISO date (YYYY-MM-DD), got {ps!r}")
        raw = data.get("budgets")
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{path}: 'budgets' must be a non-empty list")
        budgets = []
        for b in raw:
            scope = b.get("scope")
            if scope != "global" and not (
                    isinstance(scope, str) and scope.startswith("project:")):
                raise ValueError(
                    f"{path}: scope must be 'global' or 'project:<name>', got {scope!r}")
            amount = b.get("amount")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
                raise ValueError(
                    f"{path}: budget amount must be a non-negative number, got {amount!r}")
            budgets.append(BudgetEntry(scope=scope, amount=float(amount)))
        return cls(period=period, period_start=period_start, budgets=budgets)

    def amount_for(self, scope):
        for b in self.budgets:
            if b.scope == scope:
                return b.amount
        configured = [b.scope for b in self.budgets]
        raise ValueError(f"scope {scope!r} not in budget config; configured scopes: {configured}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_budget.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add budget.py tests/test_budget.py
git commit -m "feat(budget): BudgetConfig load + validation (stdlib, optional)"
```

---

### Task 2: calendar-period math

**Files:**
- Modify: `budget.py`
- Modify: `tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_budget.py`:
```python
from budget import current_period


def test_current_period_quarterly():
    anchor = datetime.date(2026, 4, 1)
    assert current_period("quarterly", anchor, datetime.date(2026, 5, 15)) == (
        datetime.date(2026, 4, 1), datetime.date(2026, 7, 1))
    assert current_period("quarterly", anchor, datetime.date(2026, 8, 1)) == (
        datetime.date(2026, 7, 1), datetime.date(2026, 10, 1))
    # several periods after the anchor
    assert current_period("quarterly", anchor, datetime.date(2027, 1, 15)) == (
        datetime.date(2027, 1, 1), datetime.date(2027, 4, 1))


def test_current_period_monthly_and_annual():
    assert current_period("monthly", datetime.date(2026, 1, 1),
                          datetime.date(2026, 3, 10)) == (
        datetime.date(2026, 3, 1), datetime.date(2026, 4, 1))
    assert current_period("annual", datetime.date(2026, 4, 1),
                          datetime.date(2028, 2, 1)) == (
        datetime.date(2027, 4, 1), datetime.date(2028, 4, 1))


def test_current_period_today_equals_anchor():
    anchor = datetime.date(2026, 4, 1)
    assert current_period("monthly", anchor, anchor) == (
        anchor, datetime.date(2026, 5, 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_budget.py -k current_period -v`
Expected: FAIL — `ImportError: cannot import name 'current_period'`

- [ ] **Step 3: Write minimal implementation**

Append to `budget.py`:
```python
def _days_in_month(year, month):
    if month == 12:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.date(year, month, 1)).days


def _add_months(d, n):
    total = (d.year * 12 + (d.month - 1)) + n
    year, month = divmod(total, 12)
    month += 1
    return datetime.date(year, month, min(d.day, _days_in_month(year, month)))


def _add_period(d, period, n):
    if period == "monthly":
        return _add_months(d, n)
    if period == "quarterly":
        return _add_months(d, n * 3)
    if period == "annual":
        return _add_months(d, n * 12)
    raise ValueError(f"unknown period {period!r}")


def current_period(period, anchor, today):
    """(start, end) of the period window containing `today`, stepping from `anchor`.
    `end` is exclusive."""
    start = anchor
    if today >= anchor:
        while True:
            nxt = _add_period(start, period, 1)
            if today < nxt:
                return start, nxt
            start = nxt
    while today < start:
        start = _add_period(start, period, -1)
    return start, _add_period(start, period, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_budget.py -k current_period -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add budget.py tests/test_budget.py
git commit -m "feat(budget): calendar-period window math"
```

---

### Task 3: spend ledger (SpendRecord + collect_spend + filters)

**Files:**
- Modify: `budget.py`
- Modify: `tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_budget.py`:
```python
from budget import SpendRecord, collect_spend, _in_scope, _in_window


def _session_jsonl(path, output_tokens, project_ts="2026-04-10T00:00:00Z"):
    rec = {"type": "assistant", "timestamp": project_ts,
           "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                       "content": [{"type": "text", "text": "x"}],
                       "usage": {"input_tokens": 0, "output_tokens": output_tokens,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 100000}}}
    path.write_text(json.dumps(rec) + "\n")


def test_collect_spend_tags_sources(tmp_path):
    real = tmp_path / "real" / "projA"
    real.mkdir(parents=True)
    _session_jsonl(real / "s.jsonl", 1000)
    runs = tmp_path / "runs"
    runs.mkdir()
    _session_jsonl(runs / "t.jsonl", 2000)

    records = collect_spend(str(tmp_path / "real"), str(runs))
    sources = sorted(r.source for r in records)
    assert sources == ["real", "tokencast"]
    assert all(r.cost > 0 for r in records)
    assert all(isinstance(r.date, datetime.date) for r in records)


def test_collect_spend_missing_dirs_are_empty():
    assert collect_spend(str("/no/such/real"), str("/no/such/runs")) == []


def test_filters():
    rec = SpendRecord(cost=1.0, project="projA", date=datetime.date(2026, 4, 10),
                      source="real")
    assert _in_scope(rec, "global") is True
    assert _in_scope(rec, "project:projA") is True
    assert _in_scope(rec, "project:other") is False
    assert _in_window(rec, datetime.date(2026, 4, 1), datetime.date(2026, 5, 1)) is True
    assert _in_window(rec, datetime.date(2026, 5, 1), datetime.date(2026, 6, 1)) is False
    no_date = SpendRecord(cost=1.0, project="p", date=None, source="real")
    assert _in_window(no_date, datetime.date(2026, 4, 1), datetime.date(2026, 5, 1)) is False
```

Note: the `real/projA/` subdir means `tokencast.load` records `project == "projA"` (it derives `project` from the parent directory name).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_budget.py -k "collect_spend or filters" -v`
Expected: FAIL — `ImportError: cannot import name 'SpendRecord'`

- [ ] **Step 3: Write minimal implementation**

Append to `budget.py`:
```python
@dataclass
class SpendRecord:
    cost: float
    project: str
    date: Optional[datetime.date]
    source: str          # "real" | "tokencast"


def _records_from(path, source):
    if not path or not os.path.exists(path):
        return []
    out = []
    for s in tokencast.load(path):
        d = None
        ts = s.get("ts_first")
        if ts:
            try:
                d = datetime.date.fromisoformat(ts[:10])
            except ValueError:
                d = None
        out.append(SpendRecord(cost=s.get("cost", 0.0), project=s.get("project", ""),
                               date=d, source=source))
    return out


def collect_spend(real_logs, runs):
    """Combined, source-tagged spend records from real Claude Code usage + TokenCast runs."""
    return _records_from(real_logs, "real") + _records_from(runs, "tokencast")


def _in_scope(record, scope):
    if scope == "global":
        return True
    if scope.startswith("project:"):
        return record.project == scope.split(":", 1)[1]
    return False


def _in_window(record, start, end):
    return record.date is not None and start <= record.date < end
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_budget.py -k "collect_spend or filters" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add budget.py tests/test_budget.py
git commit -m "feat(budget): dual-source spend ledger + scope/window filters"
```

---

### Task 4: status math (BudgetStatus + runway_tasks + fits)

**Files:**
- Modify: `budget.py`
- Modify: `tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_budget.py`:
```python
from budget import BudgetStatus, status, runway_tasks, fits


class _Cfg:
    # minimal stand-in matching BudgetConfig's attributes used by status()
    period = "quarterly"
    period_start = datetime.date(2026, 4, 1)

    def amount_for(self, scope):
        return 1000.0


def _rec(cost, source, day=10, project="projA"):
    return SpendRecord(cost=cost, project=project,
                       date=datetime.date(2026, 4, day), source=source)


def test_status_splits_sources_and_computes_remaining():
    records = [_rec(100, "real"), _rec(50, "tokencast"),
               _rec(999, "real", project="other")]  # other project excluded for project scope
    today = datetime.date(2026, 4, 20)
    st = status(_Cfg(), "global", records, today)
    assert st.consumed_real == 100 + 999       # global includes all real
    assert st.consumed_tokencast == 50
    assert st.consumed_total == 1149
    assert st.remaining == 1000.0 - 1149       # negative: over budget
    assert st.period_start == datetime.date(2026, 4, 1)
    assert st.period_end == datetime.date(2026, 7, 1)


def test_status_project_scope_and_runway():
    records = [_rec(100, "real", project="projA"), _rec(40, "tokencast", project="projA"),
               _rec(500, "real", project="other")]
    today = datetime.date(2026, 4, 20)   # 20 days elapsed
    st = status(_Cfg(), "project:projA", records, today)
    assert st.consumed_total == 140
    assert st.remaining == 860.0
    # burn = 140/20 = 7/day; runway = 860/7 ~= 122.8 days
    assert abs(st.burn_rate_per_day - 7.0) < 1e-9
    assert st.runway_days is not None and abs(st.runway_days - (860.0 / 7.0)) < 1e-6


def test_status_no_spend_has_no_burn():
    st = status(_Cfg(), "global", [], datetime.date(2026, 4, 20))
    assert st.consumed_total == 0
    assert st.burn_rate_per_day == 0.0
    assert st.runway_days is None
    assert st.projected_exhaustion is None


def test_status_over_budget_exhausted_now():
    records = [_rec(2000, "real")]
    st = status(_Cfg(), "global", records, datetime.date(2026, 4, 20))
    assert st.remaining < 0
    assert st.runway_days == 0.0
    assert st.projected_exhaustion == datetime.date(2026, 4, 20)


def test_runway_tasks_and_fits():
    assert runway_tasks(1000.0, 12.5) == 80
    assert runway_tasks(1000.0, 0) == 0
    assert runway_tasks(-5.0, 10.0) == 0
    assert fits(50.0, 40.0) is True
    assert fits(50.0, 60.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_budget.py -k "status or runway or fits" -v`
Expected: FAIL — `ImportError: cannot import name 'status'`

- [ ] **Step 3: Write minimal implementation**

Append to `budget.py`:
```python
@dataclass
class BudgetStatus:
    scope: str
    amount: float
    consumed_real: float
    consumed_tokencast: float
    consumed_total: float
    remaining: float
    period_start: datetime.date
    period_end: datetime.date
    burn_rate_per_day: float
    runway_days: Optional[float]              # None => no burn yet
    projected_exhaustion: Optional[datetime.date]  # None => won't exhaust this period


def status(config, scope, records, today):
    amount = config.amount_for(scope)
    start, end = current_period(config.period, config.period_start, today)
    in_window = [r for r in records if _in_window(r, start, end) and _in_scope(r, scope)]
    consumed_real = sum(r.cost for r in in_window if r.source == "real")
    consumed_tokencast = sum(r.cost for r in in_window if r.source == "tokencast")
    consumed_total = consumed_real + consumed_tokencast
    remaining = amount - consumed_total
    elapsed_days = max(1, (today - start).days + 1)
    burn = consumed_total / elapsed_days

    if remaining <= 0:
        runway_days = 0.0
        projected = today                                  # already over budget
    elif burn <= 0:
        runway_days = None
        projected = None
    else:
        runway_days = remaining / burn
        proj = today + datetime.timedelta(days=runway_days)
        projected = proj if proj < end else None           # None => survives the period

    return BudgetStatus(
        scope=scope, amount=amount, consumed_real=consumed_real,
        consumed_tokencast=consumed_tokencast, consumed_total=consumed_total,
        remaining=remaining, period_start=start, period_end=end,
        burn_rate_per_day=burn, runway_days=runway_days, projected_exhaustion=projected)


def runway_tasks(remaining, per_task_cost):
    """How many tasks of ~per_task_cost fit in the remaining budget (floored, >= 0)."""
    if per_task_cost <= 0 or remaining <= 0:
        return 0
    return int(remaining // per_task_cost)


def fits(remaining, forecast_cost):
    """Whether a forecasted spend fits in the remaining budget."""
    return forecast_cost <= remaining
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_budget.py -v`
Expected: PASS (all budget tests)

- [ ] **Step 5: Commit**

```bash
git add budget.py tests/test_budget.py
git commit -m "feat(budget): status math (consumed/remaining/burn/runway) + fits"
```

---

### Task 5: `tokencast.py budget` subcommand + optionality guard

**Files:**
- Modify: `tokencast.py`
- Test: `tests/test_budget_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_budget_cli.py`:
```python
import json
import types

import tokencast


def _args(**kw):
    base = dict(config="tokencast_budget.json", scope="global",
                logs="/no/such/logs", runs="/no/such/runs",
                per_task=None, forecast=None, refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_budget_no_config_prints_hint(tmp_path, capsys):
    tokencast.cmd_budget(_args(config=str(tmp_path / "absent.json")))
    out = capsys.readouterr().out
    assert "No budget configured" in out


def test_budget_with_config_prints_status(tmp_path, capsys):
    cfg = tmp_path / "tokencast_budget.json"
    cfg.write_text(json.dumps({
        "period": "annual", "period_start": "2020-01-01",   # old anchor -> always current
        "budgets": [{"scope": "global", "amount": 15000}]}))
    tokencast.cmd_budget(_args(config=str(cfg), per_task=12.5, forecast=100.0))
    out = capsys.readouterr().out
    assert "Cap" in out and "Remaining" in out
    assert "tasks" in out          # --per-task runway line
    assert "FITS" in out           # --forecast fits line (no spend -> remaining 15000)
    assert "floor" in out.lower()  # the honesty caveat


def test_existing_commands_work_without_budget(tmp_path, capsys):
    # Optionality guard: demo + report run with no budget file anywhere.
    tokencast.cmd_demo(types.SimpleNamespace(out=str(tmp_path / "logs"), sessions=8, seed=1))
    tokencast.cmd_report(types.SimpleNamespace(path=str(tmp_path / "logs"), cap=None,
                                               refresh_prices=False))
    out = capsys.readouterr().out
    assert "TokenCast" in out       # report ran normally, no budget needed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_budget_cli.py -v`
Expected: FAIL — `AttributeError: module 'tokencast' has no attribute 'cmd_budget'`

- [ ] **Step 3: Write the implementation**

In `tokencast.py`, add `cmd_budget` (place it after the existing `cmd_forecast` function, before `cmd_demo`):
```python
def cmd_budget(args):
    import budget  # lazy import avoids a tokencast<->budget cycle
    import datetime
    cfg = budget.BudgetConfig.load(args.config)
    if cfg is None:
        print(f"No budget configured. Create {args.config} to track spend against a cap "
              "(see README).")
        return
    records = budget.collect_spend(args.logs, args.runs)
    try:
        st = budget.status(cfg, args.scope, records, datetime.date.today())
    except ValueError as e:
        raise SystemExit(f"tokencast: {e}")

    print("=" * 68)
    print(f"TokenCast - budget ({st.scope})")
    print("=" * 68)
    print(f"Period      : {st.period_start} -> {st.period_end}  ({cfg.period})")
    print(f"Cap         : {money(st.amount)}")
    print(f"Consumed    : {money(st.consumed_total)}   "
          f"(real {money(st.consumed_real)} [floor] + tokencast {money(st.consumed_tokencast)})")
    print(f"Remaining   : {money(st.remaining)}")
    print(f"Burn rate   : {money(st.burn_rate_per_day)}/day")
    if st.runway_days is None:
        print("Runway      : no spend yet this period")
    else:
        exh = st.projected_exhaustion
        exh_s = exh.isoformat() if exh else "after period end (won't exhaust this period)"
        print(f"Runway      : {st.runway_days:.0f} days  (projected exhaustion: {exh_s})")
    if args.per_task is not None:
        print(f"Runway/task : ~{budget.runway_tasks(st.remaining, args.per_task)} tasks "
              f"at {money(args.per_task)}/task")
    if args.forecast is not None:
        verdict = "FITS" if budget.fits(st.remaining, args.forecast) else "does NOT fit"
        print(f"Forecast    : a sprint of {money(args.forecast)} {verdict} the remaining "
              f"{money(st.remaining)}")
    print()
    print("Note: real-usage spend uses Claude Code's JSONL, which undercounts input tokens,")
    print("so consumed-real is a FLOOR -- you may have less runway than shown.")
```

In `main()`, add the `budget` subparser. Insert this block after the existing `d.set_defaults(func=cmd_demo)` line (and before `args = ap.parse_args()`):
```python
    b = sub.add_parser("budget", help="(optional) track spend against a budget cap")
    b.add_argument("--config", default="tokencast_budget.json",
                   help="path to the budget JSON (default ./tokencast_budget.json)")
    b.add_argument("--scope", default="global", help="global | project:NAME")
    b.add_argument("--logs", default=os.path.expanduser("~/.claude/projects"),
                   help="real Claude Code logs (counts as real usage, a floor)")
    b.add_argument("--runs", default="./runs", help="TokenCast run logs (accurate)")
    b.add_argument("--per-task", type=float, default=None,
                   help="also show runway in tasks at this per-task cost")
    b.add_argument("--forecast", type=float, default=None,
                   help="also show whether a sprint of this cost fits remaining")
    b.add_argument("--refresh-prices", action="store_true",
                   help="pull current prices from the live cost map")
    b.set_defaults(func=cmd_budget)
```

> Note: `main()` already calls `refresh_prices()` when `args.refresh_prices` is set, before dispatching `args.func` — so `budget --refresh-prices` works without further changes. Do not modify the existing `forecast`/`report`/`demo` parsers or handlers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_budget_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 2 skipped live tests)

- [ ] **Step 6: Smoke-test the CLI end to end**

Run: `python3.11 tokencast.py budget --config /tmp/none.json`
Expected: prints `No budget configured. ...` and exits 0.

- [ ] **Step 7: Commit**

```bash
git add tokencast.py tests/test_budget_cli.py
git commit -m "feat(budget): tokencast.py budget subcommand (optional, lazy import)"
```

---

### Task 6: docs

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `README.md`** — add a budget section

READ `README.md`. After the core `forecast`/`report` usage (before the "Optimizer tier (preview)" section added earlier), insert:
```markdown
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
```

- [ ] **Step 2: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the eval-harness paragraph under `## 1. Fix token accuracy (the blocker)` (added in the previous sub-project), add:
```markdown
**Budget core (sub-project 3a) — done.** Optional `budget.py` + `tokencast.py budget`: a
calendar-period cap (global / per-project) with a dual-source ledger (real usage floor +
accurate TokenCast runs) reporting consumed/remaining/burn-rate/runway. The optimize loop will
consume this to frame wins as runway gained (sub-project 3b).
```

- [ ] **Step 3: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` bullet, add a new bullet:
```markdown
- `budget.py` — NEW optional light-tier module (stdlib-only). `tokencast.py budget` tracks spend
  against a calendar-period cap (global/per-project) across two labeled sources (real usage
  floor + accurate TokenCast runs), reporting consumed/remaining/burn-rate/runway. Fully
  optional: with no `tokencast_budget.json`, nothing else changes. Sub-project 3a; the
  budget-aware optimize loop (3b) imports it for runway framing.
```

- [ ] **Step 4: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 2 skipped).

- [ ] **Step 5: Commit**

```bash
git add README.md ROADMAP.md CLAUDE.md
git commit -m "docs(budget): document the optional budget feature"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `budget.py` is stdlib-only (no `claude_agent_sdk`, no `pyyaml`, no `optimize` import) and importable on its own.
- `python tokencast.py budget` with a `tokencast_budget.json` prints consumed (real-floor / tokencast / total), remaining, burn rate, and runway (days; tasks with `--per-task`); `--forecast` reports fit.
- With **no** budget file, `forecast`/`report`/`demo` behave exactly as before and `budget` prints a friendly hint and exits 0 (test-guarded).
- `tokencast.html` and everything under `optimize/` are unchanged.
