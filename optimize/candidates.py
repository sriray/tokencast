"""Candidate config sources for the optimize loop. A candidate is an AgentConfig with a
distinct config_id. Sub-project 4's generators produce AgentConfigs the same way."""
import dataclasses

from optimize.config import AgentConfig

_DEFAULT_MODELS = ("opus", "sonnet", "haiku")


def model_sweep(baseline, models=_DEFAULT_MODELS):
    """One variant per model, skipping the baseline's own model (a redundant re-eval)."""
    out = []
    for m in models:
        if m == baseline.model:
            continue
        out.append(dataclasses.replace(baseline, model=m,
                                       config_id=f"{baseline.config_id}-{m}"))
    return out


def from_dirs(paths):
    """Load an AgentConfig from each config dir."""
    return [AgentConfig.load(p) for p in paths]
