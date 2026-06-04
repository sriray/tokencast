import json

from optimize.ranking import (CandidateResult, OptimizeResult, aggregate, select,
                               pareto, build_result)
from optimize.scorer import EvalReport


def _cr(cid, quality, cost, dur=1000.0, pass_rate=1.0):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=pass_rate,
                           cost_usd=cost, duration_ms=dur, repeats=1,
                           cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality)


def test_aggregate_medians_and_p90(tmp_path):
    reports = [
        EvalReport(config_id="b", tasks=[], composite=0.8, pass_rate=1.0,
                   total_cost_usd=0.010, total_duration_ms=1000),
        EvalReport(config_id="b", tasks=[], composite=0.6, pass_rate=0.5,
                   total_cost_usd=0.030, total_duration_ms=3000),
        EvalReport(config_id="b", tasks=[], composite=0.7, pass_rate=1.0,
                   total_cost_usd=0.020, total_duration_ms=2000),
    ]
    cr = aggregate("b", reports)
    assert cr.config_id == "b"
    assert abs(cr.quality - 0.7) < 1e-9            # median composite
    assert abs(cr.pass_rate - (2.5 / 3)) < 1e-9    # mean pass_rate
    assert abs(cr.cost_usd - 0.028) < 1e-9         # p90 of [.01,.02,.03] = .028
    assert cr.repeats == 3
    assert cr.cost_min == 0.010 and cr.cost_max == 0.030


def test_select_cost_first_under_floor():
    cands = [_cr("baseline", 0.9, 0.020), _cr("cheap-good", 0.9, 0.010),
             _cr("cheap-bad", 0.4, 0.001)]
    winner_id, improved = select(cands, "baseline", floor=0.9)
    assert winner_id == "cheap-good"
    assert improved is True


def test_select_empty_eligible_falls_back_to_baseline():
    cands = [_cr("baseline", 0.5, 0.02), _cr("c", 0.6, 0.01)]
    winner_id, improved = select(cands, "baseline", floor=0.95)
    assert winner_id == "baseline"
    assert improved is False


def test_select_time_first():
    cands = [_cr("a", 0.9, 0.01, dur=5000), _cr("b", 0.9, 0.05, dur=1000)]
    winner_id, _ = select(cands, "a", floor=0.9, by="time")
    assert winner_id == "b"


def test_pareto_frontier():
    cands = [_cr("cheapbad", 0.5, 0.001), _cr("mid", 0.8, 0.010),
             _cr("dear-good", 0.9, 0.050), _cr("dominated", 0.7, 0.060)]
    front = set(pareto(cands))
    assert "cheapbad" in front and "mid" in front and "dear-good" in front
    assert "dominated" not in front


def test_build_result_deltas_and_json(tmp_path):
    cands = [_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.winner_id == "baseline-haiku"
    assert res.floor == 0.8
    assert abs(res.cost_delta_pct - (-50.0)) < 1e-9
    assert res.quality_delta == 0.0
    p = tmp_path / "optimize.json"
    res.to_json(str(p))
    data = json.loads(p.read_text())
    assert data["winner_id"] == "baseline-haiku"


def test_build_result_metric_tie_is_not_improved():
    # 'aaa' ties baseline on cost+quality, wins only the lexical id tie-break -> NOT improved
    cands = [_cr("baseline", 0.8, 0.010), _cr("aaa", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.winner_id == "aaa"
    assert res.improved is False
    assert res.cost_delta_pct == 0.0


def test_build_result_cheaper_is_improved():
    cands = [_cr("baseline", 0.8, 0.020), _cr("cheaper", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.winner_id == "cheaper"
    assert res.improved is True


from optimize.ranking import apply_budget


def test_build_result_with_budget_runway_and_gain():
    cands = [_cr("baseline", 0.8, 0.0625), _cr("baseline-haiku", 0.8, 0.03125)]
    res = build_result(cands, "baseline", budget_remaining=100.0)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline"].runway == 1600          # 100 / 0.0625 (exact in float)
    assert by["baseline-haiku"].runway == 3200    # 100 / 0.03125 (exact in float)
    assert res.budget_remaining == 100.0
    assert res.runway_gain == 1600                # winner(haiku) 3200 - baseline 1600


def test_build_result_with_need_tasks_fit_verdict():
    cands = [_cr("baseline", 0.8, 0.020), _cr("baseline-haiku", 0.8, 0.010)]
    res = build_result(cands, "baseline", budget_remaining=100.0, need_tasks=8000)
    by = {c.config_id: c for c in res.candidates}
    assert by["baseline"].fits is False          # 0.02*8000 = 160 > 100
    assert by["baseline-haiku"].fits is True      # 0.01*8000 = 80 <= 100
    assert res.need_tasks == 8000
    assert res.winner_fits is True               # winner is haiku


def test_build_result_no_budget_leaves_fields_none():
    cands = [_cr("baseline", 0.8, 0.020), _cr("c", 0.8, 0.010)]
    res = build_result(cands, "baseline")
    assert res.budget_remaining is None and res.runway_gain is None
    assert all(c.runway is None and c.fits is None for c in res.candidates)
