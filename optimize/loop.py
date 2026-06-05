"""The optimize loop: eval baseline + candidates (xN) -> rank -> budget pass -> promote."""
import os

from optimize import ranking
from optimize import candidates as candidates_mod
from optimize.evalrun import run_evalset


def run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
                 min_quality=None, by="cost", out_dir="runs", promote_to=None,
                 budget_remaining=None, need_tasks=None, generator=None, n_generated=0,
                 skills_catalog=None, mcp_catalog=None):
    def _run_config(cfg):
        reports = []
        for i in range(max(1, repeats)):
            reports.append(run_evalset(
                evalset, cfg, runner=runner, judge=judge,
                out_dir=os.path.join(out_dir, cfg.config_id, f"run_{i}")))
        return reports

    # 1. baseline first (its reports feed failure-driven generation)
    baseline_reports = _run_config(baseline)
    all_configs = [baseline]
    results = [ranking.aggregate(baseline.config_id, baseline_reports)]
    seen = {baseline.config_id}

    # 2. failure-driven generation off the baseline's reports
    working = list(candidates)
    if n_generated > 0:
        working += candidates_mod.generate_candidates(
            baseline, baseline_reports, n=n_generated, generator=generator,
            evalset=evalset, skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)

    # 3. evaluate the rest (deduped; baseline already evaluated)
    for cfg in working:
        if cfg.config_id in seen:
            continue
        seen.add(cfg.config_id)
        all_configs.append(cfg)
        results.append(ranking.aggregate(cfg.config_id, _run_config(cfg)))

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
