"""Tests for adversarial-review fixes (written before each fix — TDD).

Wave 1 — correctness & data integrity:
  BUG-1  _weighted_pct must use every weight, including the largest point's
  S1     one malformed token field must not drop the whole session
  S2     costUSD override must reject negative / non-finite / bool
  S3-5   duration uses chronological epoch order (tz offsets / naive / int safe)
"""
import json
import math

import tokencast


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# --- BUG-1: distance weighting must affect the largest point too -------------
def test_weighted_pct_top_weight_is_not_ignored():
    vals = [10.0, 100.0, 1000.0]
    light_top = tokencast._weighted_pct(vals, [1.0, 1.0, 0.01], 0.9)   # down-weight 1000
    heavy_top = tokencast._weighted_pct(vals, [1.0, 1.0, 100.0], 0.9)  # up-weight 1000
    assert heavy_top > light_top, "the largest neighbor's weight must change p90"


def test_weighted_pct_still_reduces_to_pct_for_equal_weights():
    vals = [10.0, 20.0, 30.0, 40.0, 100.0]
    w = [1.0] * len(vals)
    for q in (0.0, 0.25, 0.5, 0.9, 0.95, 1.0):
        assert math.isclose(tokencast._weighted_pct(vals, w, q),
                            tokencast.pct(vals, q), rel_tol=1e-9)


def test_weighted_pct_bounded_by_extremes():
    vals = [5.0, 50.0, 500.0]
    for w in ([1, 1, 1], [5, 1, 0.1], [0.1, 1, 5]):
        for q in (0.0, 0.3, 0.9, 1.0):
            r = tokencast._weighted_pct(vals, [float(x) for x in w], q)
            assert 5.0 <= r <= 500.0


# --- S1: a malformed token value must not erase the whole session -----------
def test_parse_session_survives_malformed_token_value(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1000, "output_tokens": 500}}},
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"output_tokens": "oops"}}},        # malformed
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))     # must not raise
    assert sess["assistant_turns"] == 3
    assert sess["output"] == 1000              # 500 + 0(bad) + 500
    assert sess["cost"] > 0


def test_parse_session_survives_non_dict_usage(tmp_path):
    p = tmp_path / "s2.jsonl"
    _write(p, [
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": [1, 2, 3]}},                        # usage not a dict
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1, "output_tokens": 200}}},
    ])
    sess = tokencast.parse_session(str(p))     # must not raise
    assert sess["output"] == 200


def test_generic_survives_malformed_token_value(tmp_path):
    p = tmp_path / "g.jsonl"
    _write(p, [
        {"timestamp": "2026-01-01T00:00:00Z", "model": "claude-sonnet-4-6",
         "usage": {"input_tokens": 5, "output_tokens": "oops"}},
        {"timestamp": "2026-01-01T00:01:00Z", "model": "claude-sonnet-4-6",
         "usage": {"input_tokens": 5, "output_tokens": 200}},
    ])
    sessions = tokencast.load_with_format(str(p), "generic")
    assert sessions and sessions[0]["output"] == 200


# --- S2: costUSD override must be sane (>=0, finite, not bool) ---------------
def test_negative_costusd_falls_back_to_computed(tmp_path):
    p = tmp_path / "c.jsonl"
    _write(p, [
        {"type": "assistant", "costUSD": -1000.0,
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    assert sess["cost"] >= 0      # not -1000


def test_bool_costusd_not_treated_as_number(tmp_path):
    p = tmp_path / "cb.jsonl"
    _write(p, [
        {"type": "assistant", "costUSD": True,
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    # True must NOT be used as cost 1.0; computed sonnet cost of 1000/500 is well under $1
    assert sess["cost"] < 1.0


# --- S3/S4/S5: duration ordered by epoch, robust to tz / naive / int --------
def test_duration_with_mixed_timezone_offsets(tmp_path):
    # 10:00+05:00 == 05:00Z; 09:00Z is 4h later -> span 240 min.
    p = tmp_path / "tz.jsonl"
    _write(p, [
        {"timestamp": "2026-01-01T09:00:00Z", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
        {"timestamp": "2026-01-01T10:00:00+05:00", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    assert sess["duration_min"] == 240.0


def test_duration_mixed_int_and_str_timestamps_no_crash(tmp_path):
    p = tmp_path / "mix.jsonl"
    _write(p, [
        {"timestamp": 1735689600, "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
        {"timestamp": "2026-01-01T00:10:00Z", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))      # must not raise
    assert sess["assistant_turns"] == 2


# ===========================================================================
# Wave 2 -- input validation (GAP-1 cap/gap-min, BUG-2/GAP-5 budget, GAP-3 count)
# ===========================================================================
import types

import pytest


def _rargs(path, **kw):
    base = dict(path=path, cap=None, segment=False, gap_min=30, split_on_user=False,
                format="auto", refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _budget_cfg(tmp_path):
    cfg = tmp_path / "b.json"
    cfg.write_text('{"period":"quarterly","period_start":"2026-04-01",'
                   '"budgets":[{"scope":"global","amount":1000}]}', encoding="utf-8")
    return cfg


def _bargs(cfg, tmp_path, **kw):
    base = dict(config=str(cfg), scope="global", logs=str(tmp_path / "none"),
                runs=str(tmp_path / "none"), per_task=None, forecast=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_report_rejects_negative_cap(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), cap=-5.0))


def test_report_rejects_nonfinite_cap(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), cap=float("nan")))


def test_report_rejects_negative_gap_min(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), segment=True, gap_min=-10))


def test_forecast_rejects_absurd_count(tmp_path):
    args = types.SimpleNamespace(path=str(tmp_path / "h"), runs=str(tmp_path / "r"),
                                 files=8, tools=30, output=None, count=10_000_000,
                                 refresh_prices=False)
    with pytest.raises(SystemExit):
        tokencast.cmd_forecast(args)


def test_budget_rejects_nonfinite_per_task(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_budget(_bargs(_budget_cfg(tmp_path), tmp_path, per_task=float("nan")))


def test_budget_rejects_negative_forecast(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_budget(_bargs(_budget_cfg(tmp_path), tmp_path, forecast=-50.0))
