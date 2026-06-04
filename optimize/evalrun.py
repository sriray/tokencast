"""Run an eval set under one config: sandbox -> harness.run -> score -> EvalReport."""
import os

from optimize import scorer as scorer_mod
from optimize.harness import run as run_task
from optimize.sandbox import task_sandbox


def run_evalset(evalset, config, *, runner=None, judge=None, out_dir="runs"):
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    for task in evalset.tasks:
        with task_sandbox(task) as cwd:
            t = {"id": task.id, "prompt": task.prompt, "cwd": cwd}
            run_result = run_task(t, config, runner=runner)
            run_result.to_jsonl(os.path.join(out_dir, f"{task.id}-{config.config_id}.jsonl"))
            score = scorer_mod.score_task(task, run_result, cwd, judge=judge)
        scores.append(score)
    report = scorer_mod.EvalReport.from_scores(config.config_id, scores)
    report.to_json(os.path.join(out_dir, "report.json"))
    return report
