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
        seen_scopes = set()
        for b in raw:
            if not isinstance(b, dict):
                raise ValueError(f"{path}: each budget must be an object, got {b!r}")
            scope = b.get("scope")
            valid_scope = scope == "global" or (
                isinstance(scope, str) and scope.startswith("project:")
                and len(scope) > len("project:"))
            if not valid_scope:
                raise ValueError(
                    f"{path}: scope must be 'global' or 'project:<name>', got {scope!r}")
            if scope in seen_scopes:
                raise ValueError(f"{path}: duplicate scope {scope!r}")
            seen_scopes.add(scope)
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
    """(start, end) of the period window containing `today`. `end` is exclusive.

    Every bound is computed as `_add_period(anchor, period, k)` from the ORIGINAL anchor (not
    cumulatively), so windows are strictly increasing, contiguous, and always satisfy
    start <= today < end -- even for anchor days 29-31 where month-end clamping applies.
    """
    k = 0
    if today >= anchor:
        while _add_period(anchor, period, k + 1) <= today:
            k += 1
    else:
        while _add_period(anchor, period, k) > today:
            k -= 1
    return _add_period(anchor, period, k), _add_period(anchor, period, k + 1)


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
