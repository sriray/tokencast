# Multi-agent readers (ROADMAP #4) — design

## Problem

TokenCast's `forecast`/`report`/`budget` layer is agent-agnostic: it consumes a list of
*session-summary* dicts and does percentile / kNN math on them. Today the only thing that
produces those summaries is the Claude Code JSONL parser (`parse_session`). ROADMAP #4 asks for
readers for other tools (Cursor, Copilot/`gh`, Codex, Aider) that emit the **same** summary shape,
so the forecast layer works unchanged across agents — only the parser differs.

## The session-summary contract

Every reader, regardless of source tool, must produce dicts of exactly the shape `reduce_entries`
already returns (so the forecast/report/budget layer needs zero changes):

```
session, project, cost,
input, output, cache_write, cache_read,
assistant_turns, tool_calls, files (a set) / files_touched (its len),
models (a set), undercount_hits, accurate (bool),
ts_first, ts_last, duration_min
```

This is the single source of truth. A reader's only job is to map *one log file* to a list of
these dicts (a list, not a single dict, so a reader is free to segment a file into multiple tasks
if its format warrants it; the Claude Code reader returns 0 or 1).

## Reader interface

A **reader** is a callable:

```python
def reader(path: str) -> list[dict]:
    """Map one log file to zero-or-more session summaries (the contract above).

    Must never raise on a malformed/foreign/empty file: return [] instead.
    The caller (load) filters out summaries with assistant_turns == 0.
    """
```

Returning `[]` is how a reader says "this file isn't mine / had nothing usable" — the loader
skips it without crashing. This keeps `--format auto` robust: it can try a reader, get `[]`, and
move on.

## Registry

A module-level dict maps a format name to its reader plus a sniffer:

```python
READERS = {
    "claude-code": Reader(read=_read_claude_code, sniff=_sniff_claude_code),
    "generic":     Reader(read=_read_generic,     sniff=_sniff_generic),
}
DEFAULT_FORMAT = "claude-code"
```

- `read(path) -> list[dict]` — the parser.
- `sniff(path) -> float` — confidence in `[0, 1]` that `path` is this format. Used only by
  `auto`. Reads just the first few lines; never raises.

Adding a future reader (Cursor, Copilot, Codex, Aider) is: write `read` + `sniff`, register them.
Nothing else in the codebase changes. The forecast/report layer never learns there are formats.

## `--format` option

`forecast` and `report` gain `--format {auto,claude-code,generic}` (default `auto`):

- `auto` (default): for each log file, pick the reader whose `sniff` scores highest above a small
  threshold; fall back to `claude-code` if nothing sniffs confidently (preserves today's behavior
  on a folder of Claude Code logs). This is per-file, so a directory mixing tools still works.
- an explicit format selects that reader for every file, skipping detection.

`budget`/`demo` are unchanged (budget reads Claude Code + accurate runs by design; demo writes
Claude Code shape).

## The `claude-code` reader = today's parser

To guarantee zero regression, the `claude-code` reader **is** `parse_session`, wrapped to return a
list: `[parse_session(path)]`. `parse_session`, `reduce_entries`, `_read_entries`, `load`,
`load_segmented` keep their existing signatures and behavior. The default code path (no `--format`,
or `--format auto` resolving to claude-code) is byte-for-byte identical to today.

`load()` gets an **optional** `fmt` parameter defaulting to `None`, which means "claude-code"
(i.e. the historical path via `parse_session`). Only when `fmt` is set does it route through the
registry. This keeps `load(root)` calls elsewhere (budget.py, optimize/) working untouched.

## The `generic` reader (the documented, minimal schema)

This is the schema README's "Extending it" section promises: any agent that logs per-message token
usage can be supported. One JSON object per line (JSONL), one object per assistant message:

```json
{"timestamp": "2026-01-01T00:00:00Z",
 "model": "gpt-some / claude-sonnet-4-6 / any id",
 "usage": {"input_tokens": 1234, "output_tokens": 567,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
 "tools": ["Edit", "Bash"],            // optional: tool-call names this message made
 "files": ["src/a.py"]}                // optional: files this message touched
```

- `model` + `usage.{input_tokens,output_tokens}` are the only required fields; cache fields default
  to 0. `tools`/`files` are optional feature signals.
- Cost is computed with the same `entry_cost`/`price_for` math as Claude Code (model-family
  pricing), so a `claude-*` id prices correctly; an unknown id falls back to Sonnet (flagged, as
  today). This keeps cost math in ONE place.
- `accurate` is `false` by default (generic logs are not harness-measured); a top-level
  `"tokencast_accurate": true` on any line marks the summary accurate, mirroring the Claude Code
  marker. The input-token undercount honesty is preserved: if a generic log also ships placeholder
  `input_tokens<=1` with `output>0`, that counts as an `undercount_hit` exactly as for Claude Code.
- `project` is the file's parent directory name, `session` the filename stem — same as Claude Code.

Wrapping a real tool (Cursor/Copilot/Codex/Aider) means writing a tiny pre-processor that emits
this generic JSONL, OR (cleaner) a dedicated reader that maps the tool's native log straight to the
summary contract. Those native readers are future work; they slot into `READERS` with no other
change. We deliberately do NOT invent unverifiable native binary formats here.

## Auto-detection (sniffing)

- `_sniff_claude_code`: scores high if early lines have `type` in {user,assistant} or a
  `message.role`, i.e. the Claude Code transcript envelope.
- `_sniff_generic`: scores high if early lines are flat objects carrying `model` + `usage` but
  *no* Claude Code `message` envelope.
- Ties / nothing-confident → `claude-code` (the safe default; matches historical behavior).

Sniffers read only the first handful of non-blank lines and swallow all errors.

## What we are NOT doing (bounded scope)

- No Cursor/Copilot/Codex/Aider native binary parsers (documented as future readers in the same
  registry; the generic schema + the registry are the extension point).
- No HTML mirror change. `tokencast.html` stays Claude-Code-focused for this item; multi-format in
  the browser is future work (noted in README).
- No change to cost math, forecast math, or the undercount caveats.

## Tests (tests/test_readers.py)

1. `claude-code` reader matches `parse_session` exactly on a CC fixture (no regression).
2. `generic` reader reduces a generic-schema fixture to the contract with correct cost/features.
3. `--format auto` picks claude-code on a CC file and generic on a generic file.
4. Unknown/empty/foreign file degrades gracefully — reader returns `[]`, `load` skips it, no crash.
5. Backward-compat: `forecast`/`report` with no `--format` produce identical output to a direct
   `load()` + same code path (default unchanged).

## Backward-compat / merge notes

- `parse_session`, `reduce_entries`, `load(root)` keep their existing call signatures. `load`
  gains an optional trailing `fmt=None` param (default = historical claude-code path).
- New code is additive: a `READERS` registry block, two sniffers, the generic reader, a
  `load_with_format` helper, and a `--format` argparse flag on `forecast`/`report`. `cmd_forecast`
  / `cmd_report` change only where they call `load(...)` (now route through the format when one is
  given).
