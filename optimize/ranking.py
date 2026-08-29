"""Pure ranking for the optimize loop: aggregate repeats, quality floor, Pareto, cost-first
selection. No eval; no I/O beyond to_json. (The budget pass is added in a later task.)"""
import dataclasses
from dataclasses import dataclass
from typing import List, Optional

import tokencast
import budget
from optimize import atomicio


@dataclass
class CandidateResult:
    config_id: str
    quality: float
    pass_rate: float
    cost_usd: float
    duration_ms: float
    repeats: int
    cost_min: float
    cost_max: float
    quality_min: float
    quality_max: float
    runway: Optional[int] = None
    fits: Optional[bool] = None


@dataclass
class OptimizeResult:
    baseline_id: str
    floor: float
    winner_id: str
    improved: bool
    candidates: List[CandidateResult]
    pareto: List[str]
    cost_delta_pct: float
    quality_delta: float
    budget_remaining: Optional[float] = None
    need_tasks: Optional[int] = None
    runway_gain: Optional[int] = None
    winner_fits: Optional[bool] = None

    def to_json(self, path):
        return atomicio.dump_json(dataclasses.asdict(self), path)


def aggregate(config_id, reports):
    composites = [r.composite for r in reports]
    costs = [r.total_cost_usd for r in reports]
    durations = [r.total_duration_ms for r in reports]
    pass_rates = [r.pass_rate for r in reports]
    n = len(reports) or 1
    return CandidateResult(
        config_id=config_id,
        quality=tokencast.pct(composites, 0.5),
        pass_rate=sum(pass_rates) / n,
        cost_usd=tokencast.pct(costs, 0.9),
        duration_ms=tokencast.pct(durations, 0.9),
        repeats=len(reports),
        cost_min=min(costs) if costs else 0.0,
        cost_max=max(costs) if costs else 0.0,
        quality_min=min(composites) if composites else 0.0,
        quality_max=max(composites) if composites else 0.0,
    )


def _by_id(candidates, cid):
    for c in candidates:
        if c.config_id == cid:
            return c
    raise KeyError(cid)


def select(candidates, baseline_id, floor, by="cost"):
    # cost_usd == 0 means the eval did not actually run (all tasks failed); such a config
    # is never a valid winner. If everything is zero-cost, eligible is empty -> baseline fallback.
    eligible = [c for c in candidates if c.quality >= floor and c.cost_usd > 0]
    if not eligible:
        return baseline_id, False
    if by == "time":
        keyfn = lambda c: (c.duration_ms, c.cost_usd, -c.quality, c.config_id)
    else:
        keyfn = lambda c: (c.cost_usd, c.duration_ms, -c.quality, c.config_id)
    winner = min(eligible, key=keyfn)
    return winner.config_id, (winner.config_id != baseline_id)


def pareto(candidates):
    # cost_usd == 0 means the eval did not actually run (all tasks failed); like select(), such
    # a config is never a valid frontier point -- otherwise it is non-dominated on cost and
    # sorts to the TOP of the table as a bogus "free" win.
    eligible = [c for c in candidates if c.cost_usd > 0]
    front = []
    for a in eligible:
        dominated = any(
            b is not a and b.cost_usd <= a.cost_usd and b.quality >= a.quality
            and (b.cost_usd < a.cost_usd or b.quality > a.quality)
            for b in eligible)
        if not dominated:
            front.append(a.config_id)
    return front


def apply_budget(result, budget_remaining, need_tasks=None):
    """Populate runway / fits / runway_gain / winner_fits in place. Pure (uses budget.runway_tasks)."""
    result.budget_remaining = budget_remaining
    result.need_tasks = need_tasks
    for c in result.candidates:
        c.runway = budget.runway_tasks(budget_remaining, c.cost_usd)
        if need_tasks is not None:
            c.fits = (c.cost_usd * need_tasks) <= budget_remaining
    winner = _by_id(result.candidates, result.winner_id)
    base = _by_id(result.candidates, result.baseline_id)
    result.runway_gain = winner.runway - base.runway
    if need_tasks is not None:
        result.winner_fits = winner.fits
    return result


def build_result(candidates, baseline_id, min_quality=None, by="cost",
                 budget_remaining=None, need_tasks=None):
    baseline = _by_id(candidates, baseline_id)
    floor = min_quality if min_quality is not None else baseline.quality
    winner_id, _ = select(candidates, baseline_id, floor, by=by)
    winner = _by_id(candidates, winner_id)
    primary_better = (winner.duration_ms < baseline.duration_ms if by == "time"
                      else winner.cost_usd < baseline.cost_usd)
    improved = winner_id != baseline_id and (primary_better or winner.quality > baseline.quality)
    cost_delta_pct = ((winner.cost_usd - baseline.cost_usd) / baseline.cost_usd * 100
                      if baseline.cost_usd else 0.0)
    result = OptimizeResult(
        baseline_id=baseline_id, floor=floor, winner_id=winner_id, improved=improved,
        candidates=candidates, pareto=pareto(candidates),
        cost_delta_pct=cost_delta_pct, quality_delta=winner.quality - baseline.quality)
    if budget_remaining is not None:
        apply_budget(result, budget_remaining, need_tasks)
    return result
