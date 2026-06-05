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


def test_live_eval_run(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK eval smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.evalset import EvalSet
    from optimize.evalrun import run_evalset

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    config = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create a file pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [
             {"name": "file", "weight": 1,
              "rule": {"kind": "file_exists", "path": "pong.txt"}},
             {"name": "quality", "weight": 1, "judge": "Did it create the file as asked? 0-1"}]}]})

    report = run_evalset(evalset, config, out_dir=str(tmp_path / "runs"))
    assert len(report.tasks) == 1
    assert 0.0 <= report.composite <= 1.0
    assert report.total_cost_usd >= 0.0


def test_live_optimize(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK optimize smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.candidates import model_sweep
    from optimize.evalset import EvalSet
    from optimize.loop import run_optimize

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\nbudget_usd: 0.10\nmax_turns: 2\n")
    baseline = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [{"name": "file", "weight": 1,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    res = run_optimize(baseline, model_sweep(baseline, models=("haiku",)), evalset,
                       out_dir=str(tmp_path / "runs"), budget_remaining=5.0)
    assert res.winner_id in (c.config_id for c in res.candidates)
    assert res.budget_remaining == 5.0


def test_live_optimize_generate(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK generate smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.evalset import EvalSet
    from optimize.loop import run_optimize

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    baseline = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [{"name": "file", "weight": 1,
                         "rule": {"kind": "file_exists", "path": "pong.txt"}}]}]})

    res = run_optimize(baseline, [], evalset, out_dir=str(tmp_path / "runs"), n_generated=1)
    assert res.winner_id in (c.config_id for c in res.candidates)
