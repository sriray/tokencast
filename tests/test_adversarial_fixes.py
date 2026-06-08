"""Tests for adversarial-review fixes (written before each fix — TDD).

Wave 1 — correctness & data integrity:
  BUG-1  _weighted_pct must use every weight, including the largest point's
  S1     one malformed token field must not drop the whole session
  S2     costUSD override must reject negative / non-finite / bool
  S3-5   duration uses chronological epoch order (tz offsets / naive / int safe)
"""
import json
import math

import tokencast


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# --- BUG-1: distance weighting must affect the largest point too -------------
def test_weighted_pct_top_weight_is_not_ignored():
    vals = [10.0, 100.0, 1000.0]
    light_top = tokencast._weighted_pct(vals, [1.0, 1.0, 0.01], 0.9)   # down-weight 1000
    heavy_top = tokencast._weighted_pct(vals, [1.0, 1.0, 100.0], 0.9)  # up-weight 1000
    assert heavy_top > light_top, "the largest neighbor's weight must change p90"


def test_weighted_pct_still_reduces_to_pct_for_equal_weights():
    vals = [10.0, 20.0, 30.0, 40.0, 100.0]
    w = [1.0] * len(vals)
    for q in (0.0, 0.25, 0.5, 0.9, 0.95, 1.0):
        assert math.isclose(tokencast._weighted_pct(vals, w, q),
                            tokencast.pct(vals, q), rel_tol=1e-9)


def test_weighted_pct_bounded_by_extremes():
    vals = [5.0, 50.0, 500.0]
    for w in ([1, 1, 1], [5, 1, 0.1], [0.1, 1, 5]):
        for q in (0.0, 0.3, 0.9, 1.0):
            r = tokencast._weighted_pct(vals, [float(x) for x in w], q)
            assert 5.0 <= r <= 500.0


# --- S1: a malformed token value must not erase the whole session -----------
def test_parse_session_survives_malformed_token_value(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1000, "output_tokens": 500}}},
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"output_tokens": "oops"}}},        # malformed
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))     # must not raise
    assert sess["assistant_turns"] == 3
    assert sess["output"] == 1000              # 500 + 0(bad) + 500
    assert sess["cost"] > 0


def test_parse_session_survives_non_dict_usage(tmp_path):
    p = tmp_path / "s2.jsonl"
    _write(p, [
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": [1, 2, 3]}},                        # usage not a dict
        {"type": "assistant", "message": {"role": "assistant", "model": "sonnet",
         "content": [], "usage": {"input_tokens": 1, "output_tokens": 200}}},
    ])
    sess = tokencast.parse_session(str(p))     # must not raise
    assert sess["output"] == 200


def test_generic_survives_malformed_token_value(tmp_path):
    p = tmp_path / "g.jsonl"
    _write(p, [
        {"timestamp": "2026-01-01T00:00:00Z", "model": "claude-sonnet-4-6",
         "usage": {"input_tokens": 5, "output_tokens": "oops"}},
        {"timestamp": "2026-01-01T00:01:00Z", "model": "claude-sonnet-4-6",
         "usage": {"input_tokens": 5, "output_tokens": 200}},
    ])
    sessions = tokencast.load_with_format(str(p), "generic")
    assert sessions and sessions[0]["output"] == 200


# --- S2: costUSD override must be sane (>=0, finite, not bool) ---------------
def test_negative_costusd_falls_back_to_computed(tmp_path):
    p = tmp_path / "c.jsonl"
    _write(p, [
        {"type": "assistant", "costUSD": -1000.0,
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    assert sess["cost"] >= 0      # not -1000


def test_bool_costusd_not_treated_as_number(tmp_path):
    p = tmp_path / "cb.jsonl"
    _write(p, [
        {"type": "assistant", "costUSD": True,
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1000, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    # True must NOT be used as cost 1.0; computed sonnet cost of 1000/500 is well under $1
    assert sess["cost"] < 1.0


# --- S3/S4/S5: duration ordered by epoch, robust to tz / naive / int --------
def test_duration_with_mixed_timezone_offsets(tmp_path):
    # 10:00+05:00 == 05:00Z; 09:00Z is 4h later -> span 240 min.
    p = tmp_path / "tz.jsonl"
    _write(p, [
        {"timestamp": "2026-01-01T09:00:00Z", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
        {"timestamp": "2026-01-01T10:00:00+05:00", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))
    assert sess["duration_min"] == 240.0


def test_duration_mixed_int_and_str_timestamps_no_crash(tmp_path):
    p = tmp_path / "mix.jsonl"
    _write(p, [
        {"timestamp": 1735689600, "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
        {"timestamp": "2026-01-01T00:10:00Z", "type": "assistant",
         "message": {"role": "assistant", "model": "sonnet", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 500}}},
    ])
    sess = tokencast.parse_session(str(p))      # must not raise
    assert sess["assistant_turns"] == 2


# ===========================================================================
# Wave 2 -- input validation (GAP-1 cap/gap-min, BUG-2/GAP-5 budget, GAP-3 count)
# ===========================================================================
import types

import pytest


def _rargs(path, **kw):
    base = dict(path=path, cap=None, segment=False, gap_min=30, split_on_user=False,
                format="auto", refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _budget_cfg(tmp_path):
    cfg = tmp_path / "b.json"
    cfg.write_text('{"period":"quarterly","period_start":"2026-04-01",'
                   '"budgets":[{"scope":"global","amount":1000}]}', encoding="utf-8")
    return cfg


def _bargs(cfg, tmp_path, **kw):
    base = dict(config=str(cfg), scope="global", logs=str(tmp_path / "none"),
                runs=str(tmp_path / "none"), per_task=None, forecast=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_report_rejects_negative_cap(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), cap=-5.0))


def test_report_rejects_nonfinite_cap(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), cap=float("nan")))


def test_report_rejects_negative_gap_min(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_report(_rargs(str(tmp_path), segment=True, gap_min=-10))


def test_forecast_rejects_absurd_count(tmp_path):
    args = types.SimpleNamespace(path=str(tmp_path / "h"), runs=str(tmp_path / "r"),
                                 files=8, tools=30, output=None, count=10_000_000,
                                 refresh_prices=False)
    with pytest.raises(SystemExit):
        tokencast.cmd_forecast(args)


def test_budget_rejects_nonfinite_per_task(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_budget(_bargs(_budget_cfg(tmp_path), tmp_path, per_task=float("nan")))


def test_budget_rejects_negative_forecast(tmp_path):
    with pytest.raises(SystemExit):
        tokencast.cmd_budget(_bargs(_budget_cfg(tmp_path), tmp_path, forecast=-50.0))


# ===========================================================================
# Wave 3 -- parser robustness (GAP-4 fences, S6 BOM, S7 sniffer, S8 pricing)
# ===========================================================================
def test_parse_plan_ignores_fenced_bullets():
    text = "- real one\n```\n- fenced\n- fenced two\n```\n- real two\n"
    assert [t.text for t in tokencast.parse_plan(text)] == ["real one", "real two"]


def test_parse_plan_ignores_tilde_fences():
    text = "- a\n~~~sh\n- not a ticket\n~~~\n- b\n"
    assert [t.text for t in tokencast.parse_plan(text)] == ["a", "b"]


def test_parse_session_handles_utf8_bom(tmp_path):
    p = tmp_path / "bom.jsonl"
    line = json.dumps({"type": "assistant", "message": {"role": "assistant",
            "model": "sonnet", "content": [],
            "usage": {"input_tokens": 1000, "output_tokens": 500}}})
    p.write_bytes(b"\xef\xbb\xbf" + line.encode("utf-8") + b"\n")
    sess = tokencast.parse_session(str(p))
    assert sess["assistant_turns"] == 1 and sess["output"] == 500


def test_auto_falls_back_when_primary_reader_extracts_nothing(tmp_path):
    # message envelope but usage at TOP level: claude-code reads $0; auto must fall back
    # to the generic reader instead of silently reporting nothing.
    p = tmp_path / "amb.jsonl"
    _write(p, [{"type": "assistant", "message": {"role": "assistant"},
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 3000, "output_tokens": 50000}}
               for _ in range(6)])
    sessions = tokencast.load_with_format(str(p), "auto")
    assert sessions and sessions[0]["output"] == 300000


def test_price_for_two_family_id_picks_earliest():
    _, key = tokencast.price_for("claude-sonnet-but-also-opus")
    assert key == "sonnet"
    _, key2 = tokencast.price_for("haiku-then-opus-blend")
    assert key2 == "haiku"


# ===========================================================================
# Wave 4 -- optimizer tier (judge NaN, provider-cost reconciliation,
#           decompose partial cost, optimize --max-spend ceiling)
# ===========================================================================
from optimize.config import AgentConfig
from optimize.candidates import model_sweep
from optimize.decompose import SubTask, Decomposition, run_decomposed
from optimize.evalset import Dimension, EvalSet
from optimize.judge import score_dimension
from optimize.loop import run_optimize
from optimize.result import RunResult


def _rr(**kw):
    base = dict(task_id="t", config_id="c", model_usage={}, cost_usd=0.0, duration_ms=0,
                num_turns=1, transcript=[], final_output="done", files_changed=[],
                accurate=True)
    base.update(kw)
    return RunResult(**base)


def _usage(out):
    return {"input_tokens": 0, "output_tokens": out,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def _runner_1k(prompt, options, cwd):
    model = options["model"]
    return {"turns": [{"model": model, "content": []}],
            "result": {"model_usage": {model: _usage(1000)}, "num_turns": 1,
                       "duration_ms": 1000, "total_cost_usd": 0.0, "result_text": "done"}}


def test_judge_nonfinite_score_is_zero_not_perfect():
    dim = Dimension.from_dict({"name": "x", "judge": "rubric 0-1"})
    assert score_dimension(_rr(), dim, judge=lambda p: float("nan")) == 0.0
    assert score_dimension(_rr(), dim, judge=lambda p: float("inf")) == 0.0


def test_runresult_cost_reconciliation():
    modeled, provider, rel = _rr(cost_usd=0.10, provider_cost_usd=0.12).cost_reconciliation()
    assert modeled == 0.10 and provider == 0.12
    assert abs(rel - (0.02 / 0.12)) < 1e-9
    assert _rr(cost_usd=0.10, provider_cost_usd=0.0).cost_reconciliation()[2] is None


def _dtask():
    return EvalSet.from_dict({"tasks": [{"id": "slug", "prompt": "Build",
        "dimensions": [{"name": "clarity", "judge": "clear? 0-1"}]}]}).tasks[0]


def test_decompose_keeps_incurred_cost_on_partial_failure():
    calls = {"i": 0}

    def runner(prompt, options, cwd):
        calls["i"] += 1
        if calls["i"] == 2:
            raise RuntimeError("step 2 failed")
        return {"turns": [{"model": options["model"], "content": []}],
                "result": {"model_usage": {"sonnet": _usage(1000)}, "num_turns": 1,
                           "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "s1"}}

    plan = Decomposition([SubTask("s1"), SubTask("s2")])
    score = run_decomposed(_dtask(), AgentConfig(config_id="b", model="sonnet"),
                           plan, runner=runner, judge=lambda p: 1.0)
    assert score.cost_usd > 0   # step-1's incurred cost is recorded, not dropped to 0


def _ml_baseline(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(d))


def test_optimize_max_spend_stops_before_candidates(tmp_path):
    baseline = _ml_baseline(tmp_path)
    cands = model_sweep(baseline)   # opus + haiku candidates
    es = EvalSet.from_dict({"tasks": [{"id": "t1", "prompt": "p",
        "dimensions": [{"name": "q", "judge": "ok? 0-1"}]}]})
    # baseline (sonnet, 1000 out) costs $0.015; a tiny ceiling stops before candidates.
    res = run_optimize(baseline, cands, es, runner=_runner_1k, judge=lambda p: 1.0,
                       out_dir=str(tmp_path / "runs"), max_spend=0.001)
    assert res.winner_id == baseline.config_id   # only baseline evaluated


# =============================================================================
# Wave 5 — under-reviewed surfaces (budget.py + optimize/{ranking,config,evalset}):
#   W5-1  budget project scope must match real mangled-path project names
#   W5-2  a run in both real logs and ./runs must not double-count
#   W5-3  a zero-cost (failed) config is not a Pareto frontier point
#   W5-4  a task with no dimensions is rejected (else 0.0-threshold "passes")
#   W5-5  a non-positive check timeout is rejected (else the check never passes)
#   W5-6  config.save round-trips skills_source (else the winner loses its skills)
# =============================================================================
import os

from budget import SpendRecord, collect_spend, _in_scope
from optimize.ranking import CandidateResult, pareto, select
from optimize.evalset import Check


def _spend_row(output_tokens):
    return {"type": "assistant", "timestamp": "2026-04-10T00:00:00Z",
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "content": [{"type": "text", "text": "x"}],
                        "usage": {"input_tokens": 0, "output_tokens": output_tokens,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 100000}}}


# --- W5-1: project scope matches the mangled-path project name ---------------
def test_budget_project_scope_matches_mangled_path():
    rec = SpendRecord(cost=1.0, project="-Users-me-dev-startups-tokencast",
                      date=None, source="real")
    assert _in_scope(rec, "project:tokencast") is True            # the documented form works
    assert _in_scope(SpendRecord(1.0, "demo-project", None, "real"),
                     "project:demo-project") is True              # exact still matches


def test_budget_project_scope_no_partial_word_match():
    rec = SpendRecord(cost=1.0, project="-Users-me-tokencast", date=None, source="real")
    assert _in_scope(rec, "project:cast") is False                # boundary guard, no false hit
    assert _in_scope(rec, "project:other") is False


# --- W5-2: same session in both real logs and ./runs collapses to accurate ----
def test_collect_spend_dedups_cross_source_session(tmp_path):
    real = tmp_path / "real" / "sandbox"
    real.mkdir(parents=True)
    runs = tmp_path / "runs" / "measured"
    runs.mkdir(parents=True)
    _write(real / "dup.jsonl", [_spend_row(1000)])   # same session id (filename) in both trees
    _write(runs / "dup.jsonl", [_spend_row(2000)])
    records = collect_spend(str(tmp_path / "real"), str(tmp_path / "runs"))
    assert len(records) == 1                          # collapsed, not double-counted
    assert records[0].source == "tokencast"           # the accurate copy wins


# --- W5-3: a zero-cost (failed) config is excluded from the Pareto front ------
def _cand(cid, quality, cost):
    return CandidateResult(config_id=cid, quality=quality, pass_rate=1.0, cost_usd=cost,
                           duration_ms=1000.0, repeats=1, cost_min=cost, cost_max=cost,
                           quality_min=quality, quality_max=quality)


def test_pareto_excludes_zero_cost_failed_config():
    base, good, failed = _cand("base", 0.9, 0.05), _cand("good", 0.95, 0.08), _cand("failed", 0.0, 0.0)
    front = pareto([base, good, failed])
    assert "failed" not in front and set(front) == {"base", "good"}
    winner, _ = select([base, good, failed], "base", 0.9)
    assert winner != "failed"


# --- W5-4: a task with no dimensions is rejected -----------------------------
def test_evalset_rejects_task_with_no_dimensions():
    with pytest.raises(ValueError, match="at least one dimension"):
        EvalSet.from_dict({"tasks": [{"id": "empty", "prompt": "do nothing",
                                      "pass_threshold": 0.0, "dimensions": []}]})


# --- W5-5: a non-positive check timeout is rejected --------------------------
def test_check_rejects_nonpositive_timeout():
    with pytest.raises(ValueError, match="timeout must be > 0"):
        Check.from_dict({"kind": "command", "cmd": "true", "timeout": 0})


# --- W5-6: save() round-trips skills_source so a reloaded winner keeps skills --
def test_config_save_roundtrips_skills_source(tmp_path):
    pytest.importorskip("yaml")
    src = tmp_path / "skillset"
    (src / "demo").mkdir(parents=True)
    (src / "demo" / "SKILL.md").write_text("# demo skill\n")
    cfg = AgentConfig(config_id="winner", model="sonnet",
                      skills_source=str(src), skills=["demo"])
    dest = tmp_path / "promoted"
    cfg.save(str(dest))
    back = AgentConfig.load(str(dest))
    assert back.skills_source == os.path.join(str(dest), "skills")
    assert os.path.exists(os.path.join(str(dest), "skills", "demo", "SKILL.md"))


# =============================================================================
# Wave 6 — fresh lenses (degenerate forecast inputs, less-trafficked commands,
#          heavy-tier security/sandbox):
#   W6-1  >=5 accurate-but-$0 runs must NOT flip to a calibrated $0 basis
#   W6-2  a 0/negative live price must not zero out a model family
#   W6-3  a path-traversal task id is rejected (arbitrary file write)
#   W6-4  a path-traversal config_id is rejected
#   W6-5  a check.path cannot escape the sandbox (read escape)
#   W6-6  symlinks in a skills dir are preserved, not dereferenced (exfiltration)
#   W6-7  JSON artifacts are written atomically
#   W6-8  markdown thematic breaks ('- - -') are not counted as plan tickets
# =============================================================================
from optimize.checks import run_check


def _fc_sess(cost, accurate):
    return {"session": "s", "project": "p", "cost": cost, "accurate": accurate}


# --- W6-1: calibration gates on cost SIGNAL, not just accurate-count ----------
def test_accurate_basis_ignores_zero_cost_accurate_runs():
    pool = [_fc_sess(0.0, True) for _ in range(5)] + [_fc_sess(1.0, False) for _ in range(5)]
    sessions, calibrated = tokencast._accurate_basis(pool)
    assert calibrated is False                       # 5 failed $0 "accurate" runs don't calibrate
    assert len(sessions) == 10                        # falls back to the full (floor) pool


def test_accurate_basis_calibrates_on_usable_accurate_runs():
    pool = [_fc_sess(2.0, True) for _ in range(5)] + [_fc_sess(1.0, False) for _ in range(3)]
    sessions, calibrated = tokencast._accurate_basis(pool)
    assert calibrated is True and len(sessions) == 5 and all(s["accurate"] for s in sessions)


# --- W6-2: a 0/negative live price never zeroes out a family -----------------
def test_family_best_skips_zero_rate_entries():
    zero = {"claude-opus-4-x": {"input_cost_per_token": 0, "output_cost_per_token": 0,
                                "litellm_provider": "anthropic"}}
    assert tokencast._family_best(zero, "opus") is None            # keep the built-in default
    good = {"claude-opus-4-x": {"input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6,
                                "litellm_provider": "anthropic"}}
    assert tokencast._family_best(good, "opus") is not None


# --- W6-3/W6-4: path-traversal ids are rejected ------------------------------
def test_evalset_rejects_path_traversal_task_id():
    for bad in ("../../etc/passwd", "/abs/path", "a/b", ".."):
        with pytest.raises(ValueError, match="safe path component"):
            EvalSet.from_dict({"tasks": [{"id": bad, "prompt": "p",
                "dimensions": [{"name": "q", "judge": "ok?"}]}]})


def test_config_rejects_path_traversal_config_id():
    for bad in ("../escape", "/abs", "a/b", ".."):
        with pytest.raises(ValueError, match="safe path component"):
            AgentConfig(config_id=bad, model="sonnet")


# --- W6-5: a check path cannot read outside the sandbox ----------------------
def test_check_path_cannot_escape_sandbox(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    assert run_check(Check(kind="file_exists", path=str(secret)), str(sbx)) is False   # absolute
    assert run_check(Check(kind="file_exists", path="../secret.txt"), str(sbx)) is False  # ..
    (sbx / "in.txt").write_text("y")
    assert run_check(Check(kind="file_exists", path="in.txt"), str(sbx)) is True       # in-sandbox


# --- W6-6: a symlink in a skills dir is preserved, not dereferenced -----------
def test_stage_skills_preserves_symlinks_no_deref(tmp_path):
    from optimize import staging
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "leak").symlink_to(secret)               # poisoned skills dir
    cfg = AgentConfig(config_id="c", model="sonnet", skills_source=str(skills))
    cwd = tmp_path / "sbx"
    cwd.mkdir()
    staging.stage_skills(cfg, str(cwd))
    staged = cwd / ".claude" / "skills" / "leak"
    assert staged.is_symlink()                          # not a copy of the secret's contents


# --- W6-7: JSON artifacts are written atomically -----------------------------
def test_atomicio_dump_json_roundtrips_no_temp_left(tmp_path):
    from optimize import atomicio
    p = tmp_path / "sub" / "out.json"
    atomicio.dump_json({"a": 1, "b": [2, 3]}, str(p))
    assert json.loads(p.read_text()) == {"a": 1, "b": [2, 3]}
    assert not list(tmp_path.glob("**/.tmp-*"))         # no leftover temp file


# --- W6-8: markdown thematic breaks are not plan tickets ---------------------
def test_parse_plan_skips_thematic_breaks():
    plan = "- real ticket one\n- - -\n* * *\n- real ticket two\n"
    assert [t.text for t in tokencast.parse_plan(plan)] == ["real ticket one", "real ticket two"]


# =============================================================================
# Wave 7 — parser/robustness parity with the browser tool (shared .py<->.html fixes):
#   W7-1  a non-string model id must not crash pricing (fallback)
#   W7-2  a negative token field must not crash forecast scaling (clamp, not ValueError)
# =============================================================================
def test_price_for_non_string_model_falls_back():
    for bad in (42, ["x"], True, {"a": 1}):
        prices, key = tokencast.price_for(bad)        # must not raise AttributeError
        assert key == "sonnet?" and prices is tokencast.PRICING["sonnet"]
    assert tokencast.price_for("claude-opus-4")[1] == "opus"   # real id still works


def test_scale_clamps_negative_token_value():
    assert tokencast._scale(-50, "output") == 0.0     # was math domain ValueError via log1p
    assert tokencast._scale(-1, "files_touched") == 0.0
    assert round(tokencast._scale(100, "output"), 3) == 4.615   # positive unchanged
