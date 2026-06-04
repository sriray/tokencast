import os

import pytest

from optimize.config import AgentConfig
from optimize.harness import run


@pytest.mark.skipif(os.environ.get("TOKENCAST_LIVE") != "1",
                    reason="set TOKENCAST_LIVE=1 to run the real-SDK smoke test (spends a little)")
def test_live_trivial_run(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    # Tiny budget so a runaway can't burn money.
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    config = AgentConfig.load(str(cfg_dir))

    task = {"id": "smoke", "prompt": "Reply with exactly the word: pong", "cwd": str(tmp_path)}
    result = run(task, config)  # uses the real default runner

    assert result.accurate is True
    assert result.model_usage  # got per-model usage back
    assert result.cost_usd >= 0.0
    assert result.num_turns >= 1
