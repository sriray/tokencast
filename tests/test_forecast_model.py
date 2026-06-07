"""Tests for the stronger forecast model (ROADMAP #3).

The model stays a kNN over standardized features but adds:
  - log-scaling of skewed features + cache_read as a feature,
  - distance-weighted percentiles + weighted Monte-Carlo sampling,
  - an adaptive k that is saner on tiny pools,
  - a neighbor-spread confidence label.

All logic lives in small pure helpers so the concurrent segmentation edit to
cmd_forecast's body does not collide.
"""
import math
import types

import tokencast


# --------------------------------------------------------------------------- #
# _weighted_pct: generalizes pct(), reduces to it with equal weights
# --------------------------------------------------------------------------- #
def test_weighted_pct_equal_weights_matches_pct():
    vals = [10.0, 20.0, 30.0, 40.0, 100.0]
    w = [1.0] * len(vals)
    for q in (0.0, 0.25, 0.5, 0.9, 0.95, 1.0):
        assert math.isclose(tokencast._weighted_pct(vals, w, q),
                            tokencast.pct(vals, q), rel_tol=1e-9)


def test_weighted_pct_monotonic():
    vals = [5.0, 9.0, 2.0, 50.0, 13.0, 8.0]
    w = [3.0, 1.0, 2.0, 0.5, 1.5, 2.0]
    p50 = tokencast._weighted_pct(vals, w, 0.5)
    p90 = tokencast._weighted_pct(vals, w, 0.9)
    p95 = tokencast._weighted_pct(vals, w, 0.95)
    assert p50 <= p90 <= p95


def test_weighted_pct_pulls_toward_heavy_weight():
    # Two clusters: cheap (heavily weighted) and expensive (light).
    vals = [10.0, 10.0, 10.0, 100.0, 100.0]
    heavy_cheap = [5.0, 5.0, 5.0, 0.1, 0.1]
    light_cheap = [0.1, 0.1, 0.1, 5.0, 5.0]
    p50_cheap = tokencast._weighted_pct(vals, heavy_cheap, 0.5)
    p50_expensive = tokencast._weighted_pct(vals, light_cheap, 0.5)
    assert p50_cheap < p50_expensive
    # heavy-cheap median should sit at the cheap cluster
    assert p50_cheap <= 10.0


def test_weighted_pct_empty_is_zero():
    assert tokencast._weighted_pct([], [], 0.5) == 0.0


# --------------------------------------------------------------------------- #
# _adaptive_k: a floor of 5, otherwise max(5, n//4)
# --------------------------------------------------------------------------- #
def test_adaptive_k_floor_is_five():
    assert tokencast._adaptive_k(6) >= 5
    assert tokencast._adaptive_k(5) == 5


def test_adaptive_k_tiny_pool_stays_sane():
    # The rule is just max(5, n//4): a small pool floors at 5 and never exceeds
    # the pool size.
    for n in (6, 7, 8):
        k = tokencast._adaptive_k(n)
        assert k == 5
        assert k <= n


def test_adaptive_k_matches_legacy_for_larger_pools():
    for n in (9, 12, 20, 40, 100):
        assert tokencast._adaptive_k(n) == max(5, n // 4)


# --------------------------------------------------------------------------- #
# _neighbor_weights: closer neighbors get more weight
# --------------------------------------------------------------------------- #
def test_neighbor_weights_monotone_decreasing():
    w = tokencast._neighbor_weights([0.0, 1.0, 2.0, 5.0])
    assert w[0] > w[1] > w[2] > w[3]
    assert all(x > 0 for x in w)


def test_neighbor_weights_equal_distances_equal():
    w = tokencast._neighbor_weights([2.0, 2.0, 2.0])
    assert math.isclose(w[0], w[1]) and math.isclose(w[1], w[2])


def test_neighbor_weights_all_zero_distance():
    # degenerate: every neighbor identical to target
    w = tokencast._neighbor_weights([0.0, 0.0, 0.0])
    assert all(x > 0 for x in w)
    assert math.isclose(w[0], w[1]) and math.isclose(w[1], w[2])


# --------------------------------------------------------------------------- #
# _scale: skewed features log-scaled, linear features untouched
# --------------------------------------------------------------------------- #
def test_scale_logs_skewed_features():
    for f in ("output", "tool_calls", "cache_read"):
        assert math.isclose(tokencast._scale(99.0, f), math.log1p(99.0))


def test_scale_linear_features_unchanged():
    for f in ("files_touched", "assistant_turns"):
        assert tokencast._scale(99.0, f) == 99.0


def test_forecast_features_includes_cache_read():
    feats = tokencast._forecast_features()
    assert "cache_read" in feats
    assert set(["files_touched", "tool_calls", "output", "assistant_turns"]).issubset(set(feats))


# --------------------------------------------------------------------------- #
# _match_label: tight vs loose neighbor spread
# --------------------------------------------------------------------------- #
def test_match_label_tight_vs_loose():
    tight = tokencast._match_label([1.0, 1.05, 0.98, 1.02])
    loose = tokencast._match_label([0.1, 2.0, 5.0, 9.0])
    assert tight == "tight"
    assert loose == "loose"


def test_match_label_returns_known_bucket():
    for ds in ([1.0], [1.0, 1.4, 1.2], [0.0, 0.0]):
        assert tokencast._match_label(ds) in {"tight", "moderate", "loose"}


# --------------------------------------------------------------------------- #
# _knn_forecast: orchestration, weighting pulls estimate toward close neighbors
# --------------------------------------------------------------------------- #
def _sess(**kw):
    base = dict(files_touched=1, tool_calls=1, output=100, assistant_turns=1,
                cache_read=1000, cost=1.0, duration_min=5.0)
    base.update(kw)
    return base


def test_knn_forecast_weights_pull_toward_close_neighbor():
    # One cluster of cheap sessions that closely matches the target, plus a few
    # far, expensive ones. Weighted p50 should sit below the unweighted p50.
    close = [_sess(files_touched=2, tool_calls=3, output=200, assistant_turns=2,
                   cache_read=2000, cost=1.0) for _ in range(4)]
    far = [_sess(files_touched=40, tool_calls=120, output=9000, assistant_turns=80,
                 cache_read=400000, cost=50.0) for _ in range(4)]
    sessions = close + far
    target = dict(files_touched=2, tool_calls=3, output=200, assistant_turns=2,
                  cache_read=2000)
    neighbors, weights, ncosts, ndurs, k, label = tokencast._knn_forecast(sessions, target)
    w_p50 = tokencast._weighted_pct(ncosts, weights, 0.5)
    u_p50 = tokencast.pct(ncosts, 0.5)
    assert w_p50 <= u_p50
    assert w_p50 <= 1.5  # should land at the cheap, close cluster


def test_knn_forecast_deterministic():
    sessions = [_sess(files_touched=i, tool_calls=i + 1, output=100 * i + 10,
                      cache_read=1000 * i + 10, cost=float(i) + 1.0,
                      assistant_turns=i + 1, duration_min=float(i) + 1.0)
                for i in range(1, 13)]
    target = dict(files_touched=5, tool_calls=6, output=510, assistant_turns=6,
                  cache_read=5010)
    a = tokencast._knn_forecast(sessions, target)
    b = tokencast._knn_forecast(sessions, target)
    assert a[2] == b[2]  # ncosts identical order
    assert a[4] == b[4]  # k identical


def test_knn_forecast_percentiles_monotonic():
    sessions = [_sess(files_touched=i, tool_calls=2 * i, output=300 * i,
                      cache_read=5000 * i, cost=float(i),
                      assistant_turns=i, duration_min=float(2 * i))
                for i in range(1, 16)]
    target = dict(files_touched=7, tool_calls=14, output=2100, assistant_turns=7,
                  cache_read=35000)
    neighbors, weights, ncosts, ndurs, k, label = tokencast._knn_forecast(sessions, target)
    p50 = tokencast._weighted_pct(ncosts, weights, 0.5)
    p90 = tokencast._weighted_pct(ncosts, weights, 0.9)
    p95 = tokencast._weighted_pct(ncosts, weights, 0.95)
    assert p50 <= p90 <= p95


# --------------------------------------------------------------------------- #
# cmd_forecast still degrades gracefully with < 5 sessions, prints confidence
# --------------------------------------------------------------------------- #
def _args(path, runs, **kw):
    base = dict(path=path, runs=runs, files=8, tools=30, output=None, count=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _write_session(d, name, marked, out=500, files=("a.py",), cache_read=50000):
    import json
    import os
    os.makedirs(d, exist_ok=True)
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": cache_read}}}
    if marked:
        line["tokencast_accurate"] = True
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def test_cmd_forecast_too_few_sessions(tmp_path, capsys):
    hist = tmp_path / "hist"
    for i in range(3):
        _write_session(str(hist), f"s{i}.jsonl", marked=False)
    runs = tmp_path / "none"
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "at least" in out.lower()


def test_cmd_forecast_prints_confidence_label(tmp_path, capsys):
    hist = tmp_path / "hist"
    for i in range(12):
        _write_session(str(hist), f"s{i}.jsonl", marked=False,
                       out=500 + i * 30, cache_read=50000 + i * 4000)
    runs = tmp_path / "none"
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out.lower()
    assert any(label in out for label in ("tight", "moderate", "loose"))


def test_cmd_forecast_percentiles_present_and_monotonic(tmp_path, capsys):
    hist = tmp_path / "hist"
    for i in range(12):
        _write_session(str(hist), f"s{i}.jsonl", marked=True,
                       out=500 + i * 30, cache_read=50000 + i * 4000)
    tokencast.cmd_forecast(_args(str(hist), str(hist)))
    out = capsys.readouterr().out
    assert "p50" in out and "p90" in out and "p95" in out
