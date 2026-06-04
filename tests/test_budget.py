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


def test_load_rejects_non_dict_entry(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                             "budgets": ["global"]})
    with pytest.raises(ValueError, match="must be an object"):
        BudgetConfig.load(path)


def test_load_rejects_empty_project_scope(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                             "budgets": [{"scope": "project:", "amount": 1}]})
    with pytest.raises(ValueError, match="scope"):
        BudgetConfig.load(path)


def test_load_rejects_duplicate_scopes(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                             "budgets": [{"scope": "global", "amount": 1},
                                         {"scope": "global", "amount": 2}]})
    with pytest.raises(ValueError, match="duplicate"):
        BudgetConfig.load(path)


def test_load_rejects_bool_amount(tmp_path):
    path = _write(tmp_path, {"period": "monthly", "period_start": "2026-04-01",
                             "budgets": [{"scope": "global", "amount": True}]})
    with pytest.raises(ValueError, match="amount"):
        BudgetConfig.load(path)


def test_load_rejects_malformed_json(tmp_path):
    p = tmp_path / "tokencast_budget.json"
    p.write_text("{ not valid json ")
    with pytest.raises(ValueError, match="tokencast_budget.json"):
        BudgetConfig.load(str(p))


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


def test_current_period_backward_clamping_anchor_contains_today():
    # anchor on a clamping day (31st), today just before it -> window MUST contain today
    start, end = current_period("monthly", datetime.date(2028, 7, 31),
                                datetime.date(2028, 7, 30))
    assert start <= datetime.date(2028, 7, 30) < end


def test_current_period_invariant_holds_for_all_anchors():
    # the core contract: start <= today < end, for clamping anchor days and both directions
    for period in ("monthly", "quarterly", "annual"):
        for aday in (28, 29, 30, 31):
            anchor = datetime.date(2027, 1, aday)   # January has 31 days, so aday is valid
            for offset in range(-400, 400, 7):
                today = anchor + datetime.timedelta(days=offset)
                start, end = current_period(period, anchor, today)
                assert start <= today < end, (period, aday, today, start, end)


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
    assert collect_spend("/no/such/real", "/no/such/runs") == []


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
               _rec(999, "real", project="other")]
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
    assert abs(st.burn_rate_per_day - 7.0) < 1e-9   # 140 / 20
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


def test_status_tiny_burn_does_not_overflow():
    # one cheap session early in the period -> runway astronomically large; must NOT crash
    records = [SpendRecord(cost=0.0001, project="projA",
                           date=datetime.date(2026, 4, 1), source="real")]
    st = status(_Cfg(), "global", records, datetime.date(2026, 4, 1))
    assert st.projected_exhaustion is None        # survives the period (no overflow)
    assert st.runway_days is not None and st.runway_days > 0


def test_status_in_period_exhaustion_returns_date():
    # high burn -> exhaustion lands within the period -> a real date in [today, period_end)
    records = [SpendRecord(cost=900.0, project="projA",
                           date=datetime.date(2026, 4, 1), source="real")]
    today = datetime.date(2026, 4, 10)            # elapsed 10 days, burn 90/day, remaining 100
    st = status(_Cfg(), "global", records, today)
    assert st.projected_exhaustion is not None
    assert today <= st.projected_exhaustion < st.period_end


def test_runway_tasks_and_fits():
    assert runway_tasks(1000.0, 12.5) == 80
    assert runway_tasks(1000.0, 0) == 0
    assert runway_tasks(-5.0, 10.0) == 0
    assert fits(50.0, 40.0) is True
    assert fits(50.0, 60.0) is False
