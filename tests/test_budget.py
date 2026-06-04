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
