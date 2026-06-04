import json
import types

import tokencast


def _args(**kw):
    base = dict(config="tokencast_budget.json", scope="global",
                logs="/no/such/logs", runs="/no/such/runs",
                per_task=None, forecast=None, refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_budget_no_config_prints_hint(tmp_path, capsys):
    tokencast.cmd_budget(_args(config=str(tmp_path / "absent.json")))
    out = capsys.readouterr().out
    assert "No budget configured" in out


def test_budget_with_config_prints_status(tmp_path, capsys):
    cfg = tmp_path / "tokencast_budget.json"
    cfg.write_text(json.dumps({
        "period": "annual", "period_start": "2020-01-01",   # old anchor -> always current
        "budgets": [{"scope": "global", "amount": 15000}]}))
    tokencast.cmd_budget(_args(config=str(cfg), per_task=12.5, forecast=100.0))
    out = capsys.readouterr().out
    assert "Cap" in out and "Remaining" in out
    assert "tasks" in out          # --per-task runway line
    assert "FITS" in out           # --forecast fits line (no spend -> remaining 15000)
    assert "floor" in out.lower()  # the honesty caveat


def test_existing_commands_work_without_budget(tmp_path, capsys):
    # Optionality guard: demo + report run with no budget file anywhere.
    tokencast.cmd_demo(types.SimpleNamespace(out=str(tmp_path / "logs"), sessions=8, seed=1))
    tokencast.cmd_report(types.SimpleNamespace(path=str(tmp_path / "logs"), cap=None,
                                               refresh_prices=False))
    out = capsys.readouterr().out
    assert "TokenCast" in out       # report ran normally, no budget needed
