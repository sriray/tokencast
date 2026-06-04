"""Combine deterministic checks + judge into a TaskScore; aggregate into an EvalReport."""
import dataclasses
import json
from dataclasses import dataclass, field
from typing import Dict, List

from optimize import checks as checks_mod
from optimize import judge as judge_mod


@dataclass
class TaskScore:
    task_id: str
    dimension_scores: Dict[str, float]
    composite: float
    passed: bool
    cost_usd: float
    duration_ms: int


@dataclass
class EvalReport:
    config_id: str
    tasks: List[TaskScore] = field(default_factory=list)
    composite: float = 0.0
    pass_rate: float = 0.0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0

    @classmethod
    def from_scores(cls, config_id, scores):
        scores = list(scores)
        n = len(scores) or 1
        return cls(
            config_id=config_id,
            tasks=scores,
            composite=sum(s.composite for s in scores) / n,
            pass_rate=sum(1 for s in scores if s.passed) / n,
            total_cost_usd=sum(s.cost_usd for s in scores),
            total_duration_ms=sum(s.duration_ms for s in scores),
        )

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(self), fh, indent=2)
        return path


def score_task(task, run_result, cwd, judge=None):
    """Score one task's RunResult. Rule checks run in `cwd`; judge dims call the judge seam."""
    dim_scores = {}
    for dim in task.dimensions:
        if dim.is_rule:
            results = [checks_mod.run_check(c, cwd) for c in dim.checks]
            dim_scores[dim.name] = sum(1 for r in results if r) / (len(results) or 1)
        else:
            dim_scores[dim.name] = judge_mod.score_dimension(run_result, dim, judge)

    total_w = sum(d.weight for d in task.dimensions) or 1.0
    composite = sum(d.weight * dim_scores[d.name] for d in task.dimensions) / total_w
    required_ok = all(dim_scores[d.name] >= 1.0 for d in task.dimensions if d.required)
    passed = composite >= task.pass_threshold and required_ok

    return TaskScore(task_id=task.id, dimension_scores=dim_scores, composite=composite,
                     passed=passed, cost_usd=run_result.cost_usd,
                     duration_ms=run_result.duration_ms)
