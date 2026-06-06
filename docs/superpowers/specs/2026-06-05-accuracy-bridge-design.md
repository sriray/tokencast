# TokenCast Light-Tier Accuracy Bridge — Design Spec

**Date:** 2026-06-05
**Status:** Approved design, pending implementation plan
**Addresses:** ROADMAP #1 (fix token accuracy — the blocker), for the light tier.
**Depends on:** the harness/`RunResult` (sub-project 1) and the existing light-tier
`forecast`/`parse_session`/`load`. All merged to `main`.

---

## 1. Why this exists

The light-tier `forecast`/`report` learn from Claude Code's JSONL, whose `input_tokens` is a
streaming placeholder (0/1 in ~75% of entries) — so their absolute numbers are a **floor**, which
the tool flags loudly. Meanwhile the heavy tier already measures runs accurately via the Agent SDK
and writes CC-schema JSONL to `./runs`. This bridge lets `forecast` **prefer those accurate runs**
when they exist, falling back to the floor history otherwise — so forecasts become trustworthy as
an engineer accumulates real harness runs. It's the smallest change that meaningfully moves
ROADMAP #1.

---

## 2. Decisions already made (don't relitigate without reason)

- **Prefer accurate, fall back to floor.** If there are ≥5 accurate sessions to calibrate on,
  `forecast` builds the estimate from accurate-only; otherwise it uses the floor pool and keeps the
  loud undercount caveat. Always labels which basis it used.
- **Accuracy is a marker the harness stamps**, not a heuristic or provenance. `RunResult.to_jsonl`
  writes `tokencast_accurate: true` into each emitted line; `parse_session` reads it. Intrinsic to
  the data — works at the CLI in any directory and in the browser; survives copy/move. Historical
  CC logs have no marker → floor.
- **Both implementations.** The CLI (`tokencast.py`) and the browser (`tokencast.html`) both get the
  prefer-accurate behavior + the marker detection, per the project's keep-both-in-sync convention.
- **No model change.** The kNN feature vector / Monte-Carlo stay exactly as today; only the *basis
  of sessions* they run on, and the labeling, change.

---

## 3. Module layout

```
optimize/result.py   MODIFY: to_jsonl stamps `tokencast_accurate: true` on each line
tokencast.py         MODIFY: parse_session tags session["accurate"]; cmd_forecast gains --runs,
                             a deduped pool, the prefer-accurate policy + honest labeling
tokencast.html       MODIFY: forecast panel mirrors the marker detection + prefer-accurate + label
README.md / CLAUDE.md / ROADMAP.md  MODIFY: document the bridge; mark ROADMAP #1 partially done
```

`tokencast.py` stays stdlib-only; nothing here imports the SDK.

---

## 4. The marker (`optimize/result.py`)

`to_jsonl` already builds CC-schema line dicts (a `user` line + per-turn `assistant` lines). Add the
top-level key `"tokencast_accurate": True` to **every** emitted line dict. It is additive and
ignored by Claude Code and by the current parser, so existing readers are unaffected. The existing
multi-model guard (raise on >1 model) and the settlement-usage logic are untouched.

## 5. Parser (`tokencast.py` `parse_session`)

- Initialize `s["accurate"] = False` in the summary dict.
- In the per-line loop, if `e.get("tokencast_accurate") is True`, set `s["accurate"] = True`
  (checked on any line — the harness stamps all of them).
- No other change. `load(root)` is unchanged; every returned session now carries `accurate`.

## 6. `forecast` (`tokencast.py` `cmd_forecast`)

- New subparser flag: `--runs` (default `./runs`), help "accurate TokenCast run logs to prefer".
- Build the candidate pool from BOTH roots and dedup by `(project, session)`:
  ```
  pool = _dedup_sessions(load(args.path) + load(args.runs))
  ```
  (Dedup so `forecast ./runs` — where `path == runs` — doesn't count each session twice.)
- Choose the basis:
  ```
  accurate = [s for s in pool if s.get("accurate")]
  basis, accurate_basis = (accurate, True) if len(accurate) >= 5 else (pool, False)
  ```
- The existing `< 5` guard now applies to `basis` (an accurate basis always has ≥5; a floor basis
  may print the existing "need ~5 historical sessions" message).
- Run the unchanged feature-standardization + kNN + Monte-Carlo on `basis`.
- **Labeling (always):**
  - accurate basis → a header line like `Calibrated on N accurate harness runs (real token counts).`
    and NO floor caveat.
  - floor basis → a caveat line like `Built on Claude Code logs that undercount input tokens — this
    is a FLOOR. Accumulate accurate runs (tokencast-optimize run/auto) to calibrate.`
  - The "Matched against k most similar past tasks (of N)" line uses the basis size.

`_dedup_sessions(sessions)` is a small helper: keep the first occurrence per `(project, session)`
key, order-preserving.

## 7. Browser mirror (`tokencast.html`)

The client-side parser tags each parsed session with `accurate` when any of its lines has
`tokencast_accurate === true`. The forecast panel applies the same policy: if ≥5 accurate sessions
are loaded, forecast on accurate-only and show an "accurate (real token counts)" badge; otherwise
forecast on all loaded sessions and show the floor caveat. Demo data remains floor (no marker).
The cost histogram / report views are unchanged except for carrying the tag.

## 8. `report` (minor)

`report` already reads accurate logs when pointed at `./runs`. This bridge adds the `accurate` tag
to its sessions but makes no required output change; the existing floor note stays (it's correct
whenever any floor session is present). No new behavior is promised for `report` here.

## 9. Testing (zero spend, no SDK)

- `optimize/result.py`: `to_jsonl` output — every line parses as JSON and carries
  `tokencast_accurate == true` (single-model fixture).
- `tokencast.py` `parse_session`: a JSONL with the marker → `session["accurate"] is True`; a plain
  CC-style JSONL (no marker) → `False`.
- `tokencast.py` `_dedup_sessions`: duplicate `(project, session)` collapses to one, order-preserving.
- `cmd_forecast`: with ≥5 marked accurate sessions in the pool → output contains "Calibrated on" and
  not the floor caveat; with only unmarked sessions → output contains the floor caveat; with
  `path == runs`, the deduped basis size is correct (no double count). Tests construct args via a
  `types.SimpleNamespace` including the new `runs` field (default to a non-existent dir to isolate
  cases).
- HTML: verified by inspection (single-file; no JS test harness) — consistent with how prior HTML
  changes were handled.
- The whole Python suite stays green with no `claude-agent-sdk` installed.

## 10. Out of scope

- The Anthropic Usage & Cost API path to accuracy (heavier; auth + dependency) — a separate future.
- Changing the kNN / feature model or sprint Monte-Carlo (ROADMAP #3).
- Expanding `report`'s output; budget's floor accounting (separate concern).

## 11. Success criteria

- After running `tokencast-optimize run`/`auto` a handful of times, `tokencast.py forecast` reports
  it is "Calibrated on N accurate runs" and drops the floor caveat; with no accurate runs it behaves
  exactly as today (floor + caveat).
- Accuracy is driven solely by the `tokencast_accurate` marker the harness writes; historical CC
  logs are always floor.
- `tokencast.html` mirrors the prefer-accurate behavior + labeling.
- `tokencast.py` stays stdlib-only; the whole suite runs zero-spend with no SDK; the heavy tier's
  cost math is unchanged.
