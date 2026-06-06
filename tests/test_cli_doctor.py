import importlib.util
import types

from optimize import cli


def test_doctor_reports_status_and_light_tier_note(capsys):
    cli.cmd_doctor(types.SimpleNamespace())
    out = capsys.readouterr().out
    assert "claude-agent-sdk installed" in out
    assert "ANTHROPIC_API_KEY" in out
    # Either it's ready (shows the live-smoke command) or it tells you how to install.
    assert ("TOKENCAST_LIVE=1" in out) or ('pip install -e ".[optimize]"' in out)
    assert "light tier" in out   # always reassures the offline core needs none of this


def test_doctor_not_ready_without_sdk(capsys, monkeypatch):
    # Force the "SDK missing" branch deterministically (cmd_doctor does `import importlib.util`
    # then calls importlib.util.find_spec, so patching it here takes effect).
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    cli.cmd_doctor(types.SimpleNamespace())
    out = capsys.readouterr().out
    assert "Not ready" in out
    assert 'pip install -e ".[optimize]"' in out
