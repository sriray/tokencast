import json
import os
import types

import tokencast


# --------------------------------------------------------------------------------------
# parse_plan
# --------------------------------------------------------------------------------------

def test_parse_plan_extracts_bullets_checkboxes_ordered():
    text = """\
# Sprint 1 plan

Some intro prose that should be ignored.

- Add OAuth login flow
* Build the settings page
+ Wire up logout
- [ ] Write integration tests
- [x] Set up CI

## Stretch

1. Dark mode
2) Export to CSV

> a blockquote, not a ticket
"""
    tickets = tokencast.parse_plan(text)
    labels = [t.text for t in tickets]
    assert labels == [
        "Add OAuth login flow",
        "Build the settings page",
        "Wire up logout",
        "Write integration tests",
        "Set up CI",
        "Dark mode",
        "Export to CSV",
    ]


def test_parse_plan_ignores_headings_blanks_prose():
    text = "# Heading\n\nplain paragraph\n\n   \n## another heading\n"
    assert tokencast.parse_plan(text) == []


def test_parse_plan_inline_hint_parsed_and_stripped():
    tickets = tokencast.parse_plan("- Refactor parser (files=8 tools=30 output=4000)\n")
    assert len(tickets) == 1
    t = tickets[0]
    assert t.text == "Refactor parser"
    assert (t.files, t.tools, t.output) == (8, 30, 4000)


def test_parse_plan_non_hint_parens_left_alone():
    tickets = tokencast.parse_plan("- Fix the bug (urgent, see #123)\n")
    assert len(tickets) == 1
    t = tickets[0]
    assert t.text == "Fix the bug (urgent, see #123)"
    assert (t.files, t.tools, t.output) == (None, None, None)


def test_parse_plan_partial_hint():
    tickets = tokencast.parse_plan("- Small tweak (tools=5)\n")
    t = tickets[0]
    assert t.text == "Small tweak"
    assert (t.files, t.tools, t.output) == (None, 5, None)


# --------------------------------------------------------------------------------------
# fixtures: history pools
# --------------------------------------------------------------------------------------

def _write(path, marked, out=500, files=("a.py",), cache_read=5000):
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": cache_read}}}
    if marked:
        line["tokencast_accurate"] = True
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _write_many(d, n, marked):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        _write(os.path.join(d, f"s{i}.jsonl"), marked, out=500 + i * 50,
               files=tuple(f"f{j}.py" for j in range(1 + i % 4)))


def _args(plan, path, runs, **kw):
    base = dict(plan=plan, path=path, runs=runs, files=8, tools=30, output=None,
                segment=False, gap_min=30, split_on_user=False, refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _plan_file(tmp_path, text):
    p = tmp_path / "plan.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------------------
# cmd_estimate
# --------------------------------------------------------------------------------------

def test_estimate_prints_per_ticket_and_sprint_total(tmp_path, capsys):
    hist = tmp_path / "hist"
    _write_many(str(hist), 12, marked=False)
    runs = tmp_path / "runs"
    os.makedirs(runs)
    plan = _plan_file(tmp_path, "- Ticket A\n- Ticket B\n- Ticket C\n")
    tokencast.cmd_estimate(_args(plan, str(hist), str(runs)))
    out = capsys.readouterr().out
    # one p90 cost+time line per ticket
    ticket_lines = [l for l in out.splitlines()
                    if l.strip().startswith("p90") and "$" in l]
    assert len(ticket_lines) == 3
    assert "Ticket A" in out and "Ticket B" in out and "Ticket C" in out
    assert "Sprint total" in out
    assert "Monte-Carlo total" in out


def test_estimate_floor_labeling(tmp_path, capsys):
    hist = tmp_path / "hist"
    _write_many(str(hist), 10, marked=False)
    runs = tmp_path / "runs"
    os.makedirs(runs)
    plan = _plan_file(tmp_path, "- One ticket\n")
    tokencast.cmd_estimate(_args(plan, str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "FLOOR" in out


def test_estimate_accurate_labeling(tmp_path, capsys):
    hist = tmp_path / "hist"
    os.makedirs(hist)
    runs = tmp_path / "runs"
    _write_many(str(runs), 6, marked=True)
    plan = _plan_file(tmp_path, "- One ticket\n")
    tokencast.cmd_estimate(_args(plan, str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "accurate harness runs" in out
    assert "FLOOR" not in out


def _cost_from_line(out):
    """Pull the first per-ticket p90 dollar amount out of estimate output."""
    for l in out.splitlines():
        if l.strip().startswith("p90") and "$" in l:
            return float(l.split("$", 1)[1].split()[0])
    raise AssertionError("no per-ticket p90 line found")


def test_estimate_inline_hint_overrides_target(tmp_path, capsys):
    # A big inline hint should match larger (costlier) neighbours than the small global
    # default, so the hinted ticket's p90 cost is strictly higher than a default-target one.
    hist = tmp_path / "hist"
    os.makedirs(hist)
    runs = tmp_path / "runs"
    os.makedirs(runs)
    # Bimodal history: half tiny+cheap, half big+costly.
    for i in range(16):
        big = i % 2 == 0
        nfiles = 12 if big else 1
        _write(os.path.join(str(hist), f"s{i}.jsonl"), marked=False,
               out=4000 if big else 50, files=tuple(f"f{j}.py" for j in range(nfiles)),
               cache_read=400_000 if big else 1000)

    plan = _plan_file(tmp_path, "- Big one (files=12 tools=200 output=4000)\n")
    tokencast.cmd_estimate(_args(plan, str(hist), str(runs), files=1, tools=1, output=10))
    hinted = capsys.readouterr().out
    assert "tools=200" in hinted  # hint echoed on the per-ticket line
    hinted_cost = _cost_from_line(hinted)

    plan2 = _plan_file(tmp_path, "- Big one\n")
    tokencast.cmd_estimate(_args(plan2, str(hist), str(runs), files=1, tools=1, output=10))
    plain = capsys.readouterr().out
    assert "tools=200" not in plain
    plain_cost = _cost_from_line(plain)

    assert hinted_cost > plain_cost  # the inline hint overrode the small global target


def test_estimate_no_tickets(tmp_path, capsys):
    hist = tmp_path / "hist"
    _write_many(str(hist), 10, marked=False)
    runs = tmp_path / "runs"
    os.makedirs(runs)
    plan = _plan_file(tmp_path, "# Just a heading\n\nprose only\n")
    tokencast.cmd_estimate(_args(plan, str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "No tickets found" in out


# --------------------------------------------------------------------------------------
# packaging
# --------------------------------------------------------------------------------------

def test_pyproject_declares_tokencast_script():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "pyproject.toml")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        import tomllib
        data = tomllib.loads(raw.decode("utf-8"))
        scripts = data["project"]["scripts"]
        assert scripts.get("tokencast") == "tokencast:main"
        assert "tokencast-optimize" in scripts  # existing entry preserved
    except ImportError:
        text = raw.decode("utf-8")
        assert 'tokencast = "tokencast:main"' in text
        assert "tokencast-optimize" in text
