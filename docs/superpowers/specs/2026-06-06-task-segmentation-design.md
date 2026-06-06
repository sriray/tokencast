# TokenCast Task Segmentation — Design Spec

**Date:** 2026-06-06
**Status:** Approved design (autonomous decision; no user available), implemented alongside.
**Addresses:** ROADMAP #2 (better task segmentation).
**Depends on:** the existing light-tier `parse_session`/`load`/`cmd_forecast`/`cmd_report`. All on `main`.

---

## 1. Why this exists

Today TokenCast treats **one JSONL file = one session = one "task"**. But a single long Claude Code
session frequently contains several distinct tasks separated by long idle gaps (the engineer walked
away, came back hours later on something new) or by new user turns. When the forecast's notion of "a
task" is "a whole multi-hour session", it doesn't match the unit a planner actually estimates (a
ticket / a feature). Segmenting a session into task-sized units makes the kNN history — and the p50/
p90 it reports — line up with how teams size work.

This is the smallest honest change that improves the *unit of estimation* without touching the
forecast model itself (that's ROADMAP #3).

---

## 2. Decisions made (don't relitigate without reason)

- **Opt-in, fully backward-compatible.** With no flag, behavior is **exactly** as today: one file =
  one session. Segmentation only happens when the user passes `--segment`. The default code path
  (`parse_session` → `load`) is untouched in shape and output.
- **Idle-gap heuristic is the primary rule.** Split a transcript wherever the wall-clock gap between
  two consecutive timestamped entries exceeds a threshold (`--gap-min`, default **30** minutes). This
  is the most robust, explainable signal: an idle gap of tens of minutes is a strong "the human
  stopped and started something else" marker, and it needs only the timestamps we already parse.
  Rejected alternatives for the *default*: pure user-turn boundaries over-segment (one task has many
  user turns); git-commit snapshots aren't reliably present in the transcript. We keep user-turn
  boundaries as an *additional, optional* refinement (`--split-on-user`) layered on top of the gap
  rule, never on its own by default.
- **Segment the raw entries, then summarize each segment exactly like a session.** A segment's cost
  and features are computed by the *same* reduction logic `parse_session` already uses — so a segment
  is indistinguishable from a session to the downstream kNN/Monte-Carlo. One file that doesn't split
  yields exactly one segment whose summary equals today's `parse_session` output (modulo a synthetic
  session id suffix only when it actually split).
- **Refactor parsing into entry-extraction + reduction, keep `parse_session` as-is.** Introduce a
  pure `reduce_entries(entries, base)` that turns a list of parsed line-dicts into one summary, and a
  pure `segment_entries(entries, gap_min, split_on_user)` that splits the entry list at gap/user
  boundaries. `parse_session` keeps its signature/output by reading lines once and calling
  `reduce_entries` over all of them. A new `parse_session_segments(path, gap_min, split_on_user)`
  returns a **list** of segment summaries. This keeps everything stdlib-only and pure/testable.
- **No model change.** The kNN feature vector, standardization, and Monte-Carlo are untouched. Only
  the *set of "tasks"* fed in changes (segments instead of whole sessions) when `--segment` is on.
- **Both implementations.** Mirror the segmentation in `tokencast.html` (vanilla JS, single-file),
  behind a checkbox + gap-minutes input, per the keep-both-in-sync convention.
- **Honesty preserved.** The input-token undercount floor caveat is unchanged. Segmenting does not
  claim better token accuracy — it only changes the unit. A short note explains the basis when on.

---

## 3. Segmentation algorithm (pure, deterministic)

Given the transcript's entries **in file order** (each a parsed JSON dict that may carry a
`timestamp`), and a threshold `gap_min`:

1. Walk entries in order, tracking the last entry that had a parseable timestamp.
2. Start a new segment **before** the current entry when either:
   - **idle gap:** the current entry has a timestamp, the previous timestamped entry exists, and
     `current - previous > gap_min` minutes; or
   - **user boundary (only if `split_on_user`):** the current entry is a `user` message *and* the
     current segment already contains at least one assistant turn with usage (so we cut between
     tasks, not on the leading user prompt or on consecutive user/tool-result lines).
3. Entries with no timestamp attach to the current segment (they don't trigger or reset the gap;
   the "previous timestamped entry" is what the gap is measured against).
4. After splitting, **drop segments with no assistant turn that has usage** (mirrors `load`'s
   existing `assistant_turns > 0` filter — a trailing idle blob of user/tool lines is not a task).

A session that never exceeds the gap (and no user split, or one task's worth of user turns) yields a
single segment == the whole session. `gap_min <= 0` disables the gap rule (only user splits, if on);
this lets the HTML/CLI express "split on user turns only" cleanly.

Segment ids: when a file produces >1 segment, segments are suffixed `#1`, `#2`, … on the session id
so report/forecast "comparable tasks" rows stay legible and dedup stays correct. A single-segment
file keeps the bare session id (so `--segment` over a corpus with no splits is a no-op for ids too).

Cost/feature additivity: because each segment is reduced by the same logic over a disjoint partition
of the entries, the **sum** of segment costs/output/tool_calls/turns equals the whole-session totals
(files_touched is per-segment set size, so it can differ — a file touched in two segments counts in
each; that is correct for a per-task unit). Tests assert the additive invariants.

---

## 4. Module layout

```
tokencast.py   MODIFY:
  - refactor: parse_session reads lines → reduce_entries(entries, base)
  - add:      segment_entries(entries, gap_min, split_on_user)  (pure)
  - add:      parse_session_segments(path, gap_min, split_on_user) -> list[summary]
  - add:      load_segmented(root, gap_min, split_on_user) -> list[summary]
  - wire:     cmd_forecast / cmd_report build their pool via load_segmented when args.segment
  - flags:    forecast & report gain --segment, --gap-min N (default 30), --split-on-user
tokencast.html MODIFY:
  - segmentEntries() + parseSessionSegments() mirror; "Segment sessions into tasks" checkbox +
    gap-minutes input; load/demo/forecast paths honor it; re-segment on toggle.
tests/test_segmentation.py  NEW (TDD).
README.md / CLAUDE.md / ROADMAP.md  MODIFY: document the flag; mark ROADMAP #2 done.
```

`tokencast.py` stays stdlib-only.

---

## 5. Parser refactor (`tokencast.py`)

- `_new_summary(session, project)` builds the empty summary dict (the `s = {...}` currently inline).
- `reduce_entries(entries, session, project)` runs the existing per-line accumulation over a list of
  already-parsed dicts and returns the finished summary (sets `files_touched`, `duration_min`).
- `parse_session(path)` becomes: read + JSON-decode lines into a list, call `reduce_entries`. Output
  is byte-for-byte the same summary as today (verified by an existing-behavior test).
- `segment_entries(entries, gap_min, split_on_user)` → `list[list[dict]]` per §3.
- `parse_session_segments(path, gap_min, split_on_user)` reads/decodes once, segments, reduces each,
  applies the `assistant_turns > 0` filter, suffixes ids only when >1 kept segment.

## 6. `load` / wiring

- `load(root)` unchanged (one summary per file).
- `load_segmented(root, gap_min, split_on_user)` globs the same way but flat-maps
  `parse_session_segments` over each file; same skip-on-exception behavior.
- `cmd_forecast`: when `args.segment`, build the pool with `load_segmented(args.path, …) +
  load_segmented(args.runs, …)` (still deduped). Add a one-line basis note:
  `Sessions segmented into tasks at >Nmin idle gaps (M tasks from K sessions).` Otherwise unchanged.
- `cmd_report`: when `args.segment`, `sessions = load_segmented(...)`; the "Sessions analyzed" label
  becomes "Tasks analyzed" and a one-line note states the gap. Otherwise unchanged.

## 7. Browser mirror (`tokencast.html`)

- `segmentEntries(entries, gapMin, splitOnUser)` + `parseSessionSegments(...)` mirror §3.
- A "Segment long sessions into tasks" checkbox + a "gap (min)" number input (default 30) near the
  forecast panel. Folder-load and demo store the raw per-entry data needed to re-segment without
  re-reading files; toggling re-segments and re-renders. Demo data keeps a single segment per
  session unless its synthetic gaps exceed the threshold (we add an occasional large gap so the demo
  visibly shows splitting).
- The forecast/report labels gain the same "segmented at >Nmin" note when on.

## 8. Testing (zero spend, no SDK; `python3.11 -m pytest`)

`tests/test_segmentation.py`:
- a transcript with one big idle gap (> gap_min) splits into 2 task summaries; under it → 1.
- no flag / default path: `load(root)` and `parse_session(path)` outputs unchanged (regression).
- additive invariants: sum of segment cost/output/tool_calls/assistant_turns == whole-session totals.
- `assistant_turns == 0` trailing segment is dropped.
- `split_on_user` cuts on a genuine new-task user turn but not on the leading prompt.
- `gap_min <= 0` disables the gap rule.
- single-segment files keep the bare id; multi-segment files get `#1/#2` suffixes; dedup still works.
- `cmd_forecast`/`cmd_report` with `segment=True` print the segmentation note and a larger task count
  than the session count for a gappy corpus.

The existing suite (forecast/report/budget/optimize) must stay green — the default path is unchanged.

## 9. Out of scope

- Git-commit-snapshot segmentation (commits aren't reliably in the transcript) — noted as a future.
- Changing the kNN / feature model or Monte-Carlo (ROADMAP #3).
- Per-segment token-accuracy improvements (ROADMAP #1) — segmentation changes the unit, not accuracy.

## 10. Success criteria

- Default behavior (no `--segment`) is byte-for-byte unchanged.
- `forecast --segment` on a corpus of long, multi-task sessions reports more, smaller "tasks" whose
  cost/time distribution better matches ticket-sized units, with an honest label of how it split.
- The segmentation function is pure, stdlib-only, deterministic, and covered by tests.
- `tokencast.html` mirrors the behavior behind an opt-in control.
- The undercount floor caveat is untouched; the whole suite runs zero-spend with no SDK.
