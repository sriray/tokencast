"""Run an eval set under one config: sandbox -> harness.run -> score -> EvalReport.

A per-task failure is isolated: it is logged to stderr and recorded as a failed
(zero-score) TaskScore so the rest of the set still scores and the optimizer still
gets a usable signal. This is a deliberate continue-on-failure batch evaluator, not a
silent swallow.
"""
import os
import sys

from optimize import scorer as scorer_mod
from optimize.harness import run as run_task
from optimize.sandbox import task_sandbox


def run_evalset(evalset, config, *, runner=None, judge=None, out_dir="runs"):
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    for task in evalset.tasks:
        try:
            with task_sandbox(task) as cwd:
                t = {"id": task.id, "prompt": task.prompt, "cwd": cwd}
                run_result = run_task(t, config, runner=runner)
                run_result.to_jsonl(
                    os.path.join(out_dir, f"{task.id}-{config.config_id}.jsonl"))
                score = scorer_mod.score_task(task, run_result, cwd, judge=judge)
        except Exception as e:  # isolate: one bad task must not abort the whole set
            print(f"  ! task {task.id!r} failed: {e}", file=sys.stderr)
            score = scorer_mod.TaskScore(task_id=task.id, dimension_scores={},
                                         composite=0.0, passed=False, cost_usd=0.0,
                                         duration_ms=0)
        scores.append(score)
    report = scorer_mod.EvalReport.from_scores(config.config_id, scores)
    report.to_json(os.path.join(out_dir, "report.json"))
    return report
