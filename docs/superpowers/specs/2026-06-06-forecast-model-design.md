# Stronger forecast model (ROADMAP #3)

**Status:** implemented.
**Scope:** the light tier `cmd_forecast` in `tokencast.py`, mirrored in `tokencast.html`.

## Problem

Today's forecast is a plain unweighted k-nearest-neighbors over 4 standardized features
(`files_touched`, `tool_calls`, `output`, `assistant_turns`): take the k nearest past sessions by
Euclidean distance, report p50/p90/p95 of their cost/duration, and Monte-Carlo a sprint total.

Three weaknesses, all fixable without abandoning the "robust on small data + explainable" virtue
that made the project pick kNN over a regression in the first place:

1. **All neighbors count equally.** The 1st and kth neighbor get the same vote, so a loosely-similar
   tail task drags the estimate as hard as the closest match. A nearer task is a better analogy and
   should weigh more.
2. **The feature set ignores the real cost driver.** Cost is dominated by cache-read tokens (see
   CLAUDE.md), yet cache tokens are not a feature, and `output` — heavily right-skewed across
   sessions — is used on a raw linear scale, so one huge session distorts standardization.
3. **No confidence signal.** A forecast off 5 tightly-clustered neighbors and one off 5 scattered
   neighbors print identically. The user can't tell a calibrated estimate from a wild guess.

## Design decisions

Keep kNN. Keep p50/p90/p95. Keep the Monte-Carlo sprint. Keep "budget the p90" and the
undercount-floor honesty. Make three explainable, small-data-robust upgrades, all the **default**
(no flag): the numbers move modestly and only in the right direction, the output stays recognizable,
and a flag would just bury the improvement.

### 1. Log-scaling skewed features

Standardize `output`, `tool_calls`, and `cache_read` on `log1p` rather than raw value. These three
are strongly right-skewed (a handful of giant sessions, a long body of small ones); on a linear
scale their z-scores are dominated by the few outliers, so distance is effectively measured in
"how far from the biggest session" rather than "how similar to the target". `log1p` compresses the
tail so distance reflects proportional similarity. `files_touched` and `assistant_turns` stay linear
(small bounded counts, not heavy-tailed). The target's feature values are log-scaled the same way
before distancing. This is a coordinate transform, not a new model — fully explainable.

### 2. Add `cache_read` as a feature (the cost driver)

Cost is overwhelmingly cache-read tokens. Two sessions with identical files/tools/turns but 10x
different cache-read are not comparable cost analogies, yet today they're neighbors. Adding
`cache_read` (log-scaled, per #1) makes "similar" mean "similar cost shape", which is the whole
point of a cost forecast. Five features now: `files_touched`, `tool_calls`, `output`,
`assistant_turns`, `cache_read`. The target's `cache_read` defaults to the matched-pool mean (the
user typically only supplies files + tools), exactly as `output`/`turns` already default.

### 3. Distance-weighted percentiles + neighbor-spread confidence

Replace the unweighted percentile over neighbors with a **distance-weighted percentile**: each
neighbor gets weight `1 / (1 + d_norm)` where `d_norm` is its distance divided by the median
neighbor distance (so weights are scale-free and the closest neighbors dominate). The weighted
percentile walks the cumulative weight instead of cumulative count. With equal distances this
reduces to the old unweighted percentile, so behavior degrades gracefully and stays a strict
generalization. The Monte-Carlo sprint samples neighbors **proportional to the same weights** (so
the sprint and per-task views agree on which neighbors matter).

Confidence signal: report the **neighbor-distance spread** as a coefficient of variation
(`stdev(distances) / mean(distances)`) bucketed into a one-word label — `tight` / `moderate` /
`loose` match. Tight = the k neighbors really look like your task; loose = thin/scattered history,
treat the p90 as soft. One extra line; no new flags.

### Adaptive k (small-data sanity)

`k = max(5, n // 4)` over-samples tiny pools: with n=6 it forces k=5, i.e. 83% of all history counts
as "nearest", which is barely a neighborhood. Cap k so it never exceeds ~60% of the pool:
`k = min(max(5, n // 4), max(5, (3 * n) // 5))`. For n >= 9 this is identical to today (the `n//4`
term wins); it only kicks in on very small pools and never drops k below the existing floor of 5,
so existing tests that assert `(of N)` / `k` lines on >= 9-session pools are unaffected.

## Why default, not flagged

The changes are monotone improvements that keep the same output shape and the same p50/p90/p95
columns. A flag would (a) split the codebase the segmentation agent is also editing and (b) hide the
better model behind discoverability. The one genuinely new surface — the confidence label — is
additive (one line), not a changed number, so it can't break a downstream reader.

## Isolation (concurrent segmentation work)

All model logic lives in new pure helpers so the concurrent SEGMENTATION agent's edits to
`cmd_forecast`'s body don't collide:

- `_forecast_features()` — returns the feature list + per-feature scaling fn (log vs linear).
- `_scale(value, feature)` — apply the per-feature transform.
- `_adaptive_k(n)` — the capped k.
- `_weighted_pct(values, weights, q)` — distance-weighted percentile (generalizes `pct`).
- `_neighbor_weights(distances)` — `1/(1+d/median)` weights.
- `_match_label(distances)` — `tight`/`moderate`/`loose`.
- `_knn_forecast(sessions, target)` — orchestrates: scale, distance, adaptive-k, neighbors,
  weights; returns `(neighbors, weights, ncosts, ndurs, k, label)`.

`cmd_forecast` calls `_knn_forecast(...)` and uses `_weighted_pct` instead of `pct` for the cost/time
lines and weighted sampling for the sprint; the printing/flow is otherwise untouched. The HTML
`forecast()` mirrors the same helpers in JS.

## Tests (`tests/test_forecast_model.py`)

- distance-weighting pulls the estimate toward the closest neighbors (weighted p50 nearer the close
  cluster than the unweighted p50).
- `_weighted_pct` with equal weights == `pct` (graceful reduction).
- weighted percentiles stay monotonic: p50 <= p90 <= p95.
- `_adaptive_k` caps on tiny pools, matches `max(5, n//4)` for n >= 9, floors at 5.
- log-scaling is applied to the skewed features and not the linear ones.
- `_match_label` buckets tight vs loose neighbor spreads correctly.
- deterministic: same inputs -> same outputs (Monte-Carlo seeded).
- degrades gracefully with < 5 sessions (existing "need ~5" path preserved).
