"""The optimize loop: eval baseline + candidates (xN) -> rank -> budget pass -> promote."""
import os

from optimize import ranking
from optimize.evalrun import run_evalset


def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None):
    # de-dup by config_id, baseline first
    all_configs = [baseline]
    seen = {baseline.config_id}
    for c in candidates:
        if c.config_id not in seen:
            all_configs.append(c)
            seen.add(c.config_id)

    results = []
    for cfg in all_configs:
        reports = []
        for i in range(max(1, repeats)):
            reports.append(run_evalset(
                evalset, cfg, runner=runner, judge=judge,
                out_dir=os.path.join(out_dir, cfg.config_id, f"run_{i}")))
        results.append(ranking.aggregate(cfg.config_id, reports))

    result = ranking.build_result(results, baseline.config_id, min_quality=min_quality,
                                  by=by, budget_remaining=budget_remaining,
                                  need_tasks=need_tasks)
    os.makedirs(out_dir, exist_ok=True)
    result.to_json(os.path.join(out_dir, "optimize.json"))

    winner_cfg = next(c for c in all_configs if c.config_id == result.winner_id)
    winner_cfg.save(os.path.join(out_dir, "promoted"))
    if promote_to:
        winner_cfg.save(promote_to)
    return result
