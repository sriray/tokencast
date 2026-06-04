"""Pure ranking for the optimize loop: aggregate repeats, quality floor, Pareto, cost-first
selection. No eval; no I/O beyond to_json. (The budget pass is added in a later task.)"""
import dataclasses
import json
from dataclasses import dataclass, field
from typing import List, Optional

import tokencast


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

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(self), fh, indent=2)
        return path


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
    eligible = [c for c in candidates if c.quality >= floor]
    if not eligible:
        return baseline_id, False
    if by == "time":
        keyfn = lambda c: (c.duration_ms, c.cost_usd, -c.quality, c.config_id)
    else:
        keyfn = lambda c: (c.cost_usd, c.duration_ms, -c.quality, c.config_id)
    winner = min(eligible, key=keyfn)
    return winner.config_id, (winner.config_id != baseline_id)


def pareto(candidates):
    front = []
    for a in candidates:
        dominated = any(
            b is not a and b.cost_usd <= a.cost_usd and b.quality >= a.quality
            and (b.cost_usd < a.cost_usd or b.quality > a.quality)
            for b in candidates)
        if not dominated:
            front.append(a.config_id)
    return front


def build_result(candidates, baseline_id, min_quality=None, by="cost"):
    baseline = _by_id(candidates, baseline_id)
    floor = min_quality if min_quality is not None else baseline.quality
    winner_id, improved = select(candidates, baseline_id, floor, by=by)
    winner = _by_id(candidates, winner_id)
    cost_delta_pct = ((winner.cost_usd - baseline.cost_usd) / baseline.cost_usd * 100
                      if baseline.cost_usd else 0.0)
    return OptimizeResult(
        baseline_id=baseline_id, floor=floor, winner_id=winner_id, improved=improved,
        candidates=candidates, pareto=pareto(candidates),
        cost_delta_pct=cost_delta_pct, quality_delta=winner.quality - baseline.quality)
