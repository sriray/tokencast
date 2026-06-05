"""run_auto: the deterministic front-door chain. Runs the optimize loop, promotes the winner,
and (optionally) compares task decompositions on that winner, writing a combined auto.json.
Pure glue over run_optimize / run_decompose; the forecast + confirmation live in the CLI."""
import dataclasses
import json
import os

from optimize.config import AgentConfig
from optimize.decompose import run_decompose
from optimize.loop import run_optimize


def run_auto(baseline, evalset, *, runner=None, judge=None, generator=None, decomposer=None,
             candidates=None, repeats=1, n_generated=0, with_decompose=False, n_decompose=None,
             min_quality=None, by="cost", out_dir="runs", promote_to=None, budget_remaining=None,
             need_tasks=None, skills_catalog=None, mcp_catalog=None):
    """Optimize configs (run_optimize writes optimize.json + promoted/), then — if
    with_decompose — compare task decompositions on the promoted winner. The decomposition
    count is its own knob (n_decompose), independent of the optimize axis's n_generated;
    it defaults to 2 (when None) so --decompose tries real decompositions out of the box rather
    than the old no-op of 0. (An explicit n_decompose=0 still means monolithic-only, matching
    the standalone `decompose` command.) Returns
    {"winner_id", "optimize": OptimizeResult, "decompose": list|None} and writes auto.json."""
    result = run_optimize(
        baseline, candidates or [], evalset, runner=runner, judge=judge, generator=generator,
        repeats=repeats, min_quality=min_quality, by=by, out_dir=out_dir, promote_to=promote_to,
        budget_remaining=budget_remaining, need_tasks=need_tasks, n_generated=n_generated,
        skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)

    decompose = None
    if with_decompose:
        winner_cfg = AgentConfig.load(os.path.join(out_dir, "promoted"))
        decompose = run_decompose(
            evalset, winner_cfg, n=(2 if n_decompose is None else n_decompose), runner=runner,
            judge=judge, decomposer=decomposer, min_quality=min_quality, by=by, out_dir=out_dir)

    os.makedirs(out_dir, exist_ok=True)
    summary = {"winner_id": result.winner_id, "optimize": dataclasses.asdict(result),
               "decompose": decompose}
    with open(os.path.join(out_dir, "auto.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return {"winner_id": result.winner_id, "optimize": result, "decompose": decompose}
