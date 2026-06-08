#!/usr/bin/env python3
"""
TokenCast - predict what an agentic coding task will COST and how LONG it takes,
so you can build software estimates and plan timelines in the metered era.

Estimation used to be a human sizing a ticket. That no longer predicts the bill:
a Microsoft/Stanford study found human-rated difficulty only weakly tracks actual
token cost, and the same task can vary up to 30x. So TokenCast does not ask you to
guess. It reads the session logs your coding agent already writes to disk, learns
what tasks *like the one you're planning* have actually cost (and taken) before,
and returns a forecast as a RANGE -- p50/p90/p95 -- plus a sprint/project total.

`forecast` is the primary command. `report` (spend attribution) is a secondary view;
tools like ccusage already do attribution well. The forward-looking estimate is the
part that's missing elsewhere.

Today it targets Claude Code's JSONL transcripts (~/.claude/projects). Absolute costs
are a FLOOR because Claude Code undercounts input tokens; run tasks through the
optimizer tier (tokencast-optimize) to measure them accurately and calibrate.

No third-party dependencies. Python 3.8+.

Usage:
    python tokencast.py demo --out ./sample_logs                 # generate fake logs
    python tokencast.py forecast ./sample_logs --files 8 --tools 30   # estimate one task
    python tokencast.py forecast ./sample_logs --files 8 --tools 30 --count 12  # a sprint
    python tokencast.py forecast ~/.claude/projects --files 12   # on your real logs
    python tokencast.py report ./sample_logs                     # (secondary) past spend
"""

import argparse, glob, json, math, os, random, re, statistics, sys
from collections import defaultdict, namedtuple

# --- Pricing (USD per 1M tokens). VERIFY against platform.claude.com/docs pricing;
#     these reflect rates as of mid-2026 and WILL drift. Edit them to match current pricing. ---
PRICING = {
    "opus":   {"input": 5.0,  "output": 25.0},   # Opus 4.7 / 4.8
    "sonnet": {"input": 3.0,  "output": 15.0},   # Sonnet 4.6
    "haiku":  {"input": 1.0,  "output": 5.0},    # Haiku 4.5
}
CACHE_WRITE_MULT = 1.25   # cache_creation_input_tokens billed at 1.25x base input (5-min cache)
CACHE_READ_MULT  = 0.10   # cache_read_input_tokens billed at 0.10x base input (90% off)
FILE_TOOLS = {"Edit", "Write", "Read", "MultiEdit", "NotebookEdit"}

# --- Optional: pull current prices from the community LiteLLM cost map. Anthropic
#     publishes no machine-readable feed, so this is the de-facto source (day-0 updated).
#     Falls back to a local cache, then to the built-in defaults above. ---
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
PRICE_CACHE = os.path.expanduser("~/.tokencast_prices.json")


def _family_best(j, fam):
    """Pick the representative (priciest, i.e. current) entry for a model family."""
    best = None
    for k, v in j.items():
        if not isinstance(v, dict):
            continue
        prov = v.get("litellm_provider", "")
        if prov and prov != "anthropic":
            continue
        if fam not in k.lower():
            continue
        ic, oc = v.get("input_cost_per_token"), v.get("output_cost_per_token")
        if not isinstance(ic, (int, float)) or not isinstance(oc, (int, float)):
            continue
        if best is None or ic > best.get("input_cost_per_token", 0):
            best = v
    return best


def _apply_price_data(data):
    global CACHE_WRITE_MULT, CACHE_READ_MULT
    for fam, vals in data.get("models", {}).items():
        if fam in PRICING:
            PRICING[fam] = vals
    if "cw" in data:
        CACHE_WRITE_MULT = data["cw"]
    if "cr" in data:
        CACHE_READ_MULT = data["cr"]


def refresh_prices(verbose=True):
    """Fetch live prices; on failure use cache; on failure of that, keep defaults."""
    import urllib.request, datetime
    try:
        with urllib.request.urlopen(LITELLM_URL, timeout=15) as r:
            j = json.loads(r.read().decode())
    except Exception as ex:
        if os.path.exists(PRICE_CACHE):
            try:
                data = json.load(open(PRICE_CACHE))
                _apply_price_data(data)
                if verbose:
                    print(f"(live fetch failed: {ex}; using cached prices from {data.get('_fetched','?')})",
                          file=sys.stderr)
                return
            except Exception:
                pass
        if verbose:
            print(f"(live fetch failed: {ex}; using built-in defaults)", file=sys.stderr)
        return
    data = {"_fetched": datetime.date.today().isoformat(), "models": {}}
    for fam in ("opus", "sonnet", "haiku"):
        b = _family_best(j, fam)
        if not b:
            continue
        data["models"][fam] = {"input": b["input_cost_per_token"] * 1e6,
                               "output": b["output_cost_per_token"] * 1e6}
        if fam == "sonnet":
            ic = b["input_cost_per_token"]
            cw, cr = b.get("cache_creation_input_token_cost"), b.get("cache_read_input_token_cost")
            if isinstance(cw, (int, float)) and ic:
                data["cw"] = cw / ic
            if isinstance(cr, (int, float)) and ic:
                data["cr"] = cr / ic
    _apply_price_data(data)
    try:
        json.dump(data, open(PRICE_CACHE, "w"))
    except Exception:
        pass
    if verbose:
        print(f"(prices: live from LiteLLM cost map, {data['_fetched']})", file=sys.stderr)


def price_for(model):
    m = (model or "").lower()
    for key in PRICING:
        if key in m:
            return PRICING[key], key
    return PRICING["sonnet"], "sonnet?"  # fallback; flagged in output


def _num(x):
    """Token/count fields must be numbers; anything else (str, list, None, bool) -> 0.

    Guards the parsers against a malformed JSONL value silently crashing (and thereby
    dropping) an entire otherwise-valid session.
    """
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _valid_cost(v):
    """A usable explicit cost override: a finite, non-negative real (not a bool)."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v >= 0)


def entry_cost(usage, model):
    p, _ = price_for(model)
    inp  = _num(usage.get("input_tokens"))
    out  = _num(usage.get("output_tokens"))
    cw   = _num(usage.get("cache_creation_input_tokens"))
    cr   = _num(usage.get("cache_read_input_tokens"))
    return (inp * p["input"]
            + cw * p["input"] * CACHE_WRITE_MULT
            + cr * p["input"] * CACHE_READ_MULT
            + out * p["output"]) / 1_000_000.0


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------
def _new_summary(session, project):
    """An empty session/segment summary dict."""
    return {
        "session": session,
        "project": project,
        "cost": 0.0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0,
        "assistant_turns": 0, "tool_calls": 0, "files": set(),
        "models": set(), "undercount_hits": 0, "accurate": False, "ts_first": None, "ts_last": None,
        "_ep_first": None, "_ep_last": None,
    }


def _track_ts(s, ts):
    """Record first/last timestamps by CHRONOLOGICAL epoch, not lexical string order.

    Keeps the raw string (report slices ts_first[:10] for by-day grouping) but decides
    earliest/latest by parsed epoch -- robust to mixed tz offsets, naive vs aware, and
    numeric-vs-string timestamps (which previously crashed min()/max()).
    """
    ep = _epoch(ts)
    if ep is None:
        return
    if s["_ep_first"] is None or ep < s["_ep_first"]:
        s["_ep_first"], s["ts_first"] = ep, ts
    if s["_ep_last"] is None or ep > s["_ep_last"]:
        s["_ep_last"], s["ts_last"] = ep, ts


def _finalize_duration(s):
    """Wall-clock minutes from the tracked epoch span (rough: includes idle/think time)."""
    a, b = s["_ep_first"], s["_ep_last"]
    s["duration_min"] = round((b - a) / 60.0, 1) if (a is not None and b is not None) else None


def reduce_entries(entries, session, project):
    """Reduce a list of already-parsed JSONL dicts to one summary with cost + features.

    Pure: identical for a whole session or a single segment of one (see segment_entries).
    """
    s = _new_summary(session, project)
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("tokencast_accurate") is True:
            s["accurate"] = True
        _track_ts(s, e.get("timestamp"))
        msg = e.get("message") or {}
        if e.get("type") == "assistant" or msg.get("role") == "assistant":
            usage = msg.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            model = msg.get("model") or e.get("model")
            if usage:
                s["assistant_turns"] += 1
                s["input"]       += _num(usage.get("input_tokens"))
                s["output"]      += _num(usage.get("output_tokens"))
                s["cache_write"] += _num(usage.get("cache_creation_input_tokens"))
                s["cache_read"]  += _num(usage.get("cache_read_input_tokens"))
                if model:
                    s["models"].add(model)
                # Prefer an explicit cost field if the tool wrote a sane one; else compute.
                s["cost"] += e.get("costUSD") if _valid_cost(e.get("costUSD")) \
                    else entry_cost(usage, model)
                # Known Claude Code bug: input_tokens is a streaming placeholder,
                # often 0/1 while real input lives in the cache fields.
                if _num(usage.get("output_tokens")) > 0 and _num(usage.get("input_tokens")) <= 1:
                    s["undercount_hits"] += 1
            # tool calls + files touched, from content blocks
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        s["tool_calls"] += 1
                        if block.get("name") in FILE_TOOLS:
                            fp = (block.get("input") or {}).get("file_path") \
                                or (block.get("input") or {}).get("notebook_path")
                            if fp:
                                s["files"].add(fp)
    s["files_touched"] = len(s["files"])
    _finalize_duration(s)
    return s


def _read_entries(path):
    """Read a JSONL transcript into a list of parsed dicts, skipping unparseable lines."""
    entries = []
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def parse_session(path):
    """Reduce one JSONL transcript to a session summary with cost + features."""
    return reduce_entries(_read_entries(path),
                          os.path.splitext(os.path.basename(path))[0],
                          os.path.basename(os.path.dirname(path)))


# --------------------------------------------------------------------------------------
# Segmentation (ROADMAP #2): split one transcript into task-sized units.
# --------------------------------------------------------------------------------------
def _epoch(ts):
    """Parse a timestamp to a UTC epoch (seconds). Naive timestamps are treated as UTC
    so the result is deterministic (not dependent on the host timezone). None on failure."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _has_usage(entry):
    if not isinstance(entry, dict):
        return False
    msg = entry.get("message") or {}
    if entry.get("type") == "assistant" or msg.get("role") == "assistant":
        return bool(msg.get("usage"))
    return False


def segment_entries(entries, gap_min=30, split_on_user=False):
    """Split a transcript's entries (file order) into one-or-more task segments.

    Pure & deterministic. Splits BEFORE an entry when either:
      - idle gap: gap_min > 0 and this entry's timestamp is > gap_min minutes after the
        previous *timestamped* entry; or
      - user boundary (only if split_on_user): this entry is a fresh user turn AND the
        current segment already holds an assistant turn with usage (so we cut between
        tasks, not on the leading prompt or on consecutive user/tool-result lines).

    Entries without a parseable timestamp attach to the current segment and don't move the
    gap reference. Returns a list of lists (never empty unless `entries` is empty).
    """
    if not entries:
        return []
    gap_s = gap_min * 60.0 if gap_min and gap_min > 0 else None
    segments, cur = [], []
    last_ts = None        # epoch of the previous timestamped entry
    cur_has_turn = False  # has the current segment seen assistant usage yet
    for e in entries:
        ts = _epoch((e or {}).get("timestamp")) if isinstance(e, dict) else None
        split = False
        if gap_s is not None and ts is not None and last_ts is not None and (ts - last_ts) > gap_s:
            split = True
        if (not split) and split_on_user and cur_has_turn and isinstance(e, dict):
            msg = e.get("message") or {}
            if e.get("type") == "user" or msg.get("role") == "user":
                split = True
        if split and cur:
            segments.append(cur)
            cur, cur_has_turn = [], False
        cur.append(e)
        if _has_usage(e):
            cur_has_turn = True
        if ts is not None:
            last_ts = ts
    if cur:
        segments.append(cur)
    return segments


def parse_session_segments(path, gap_min=30, split_on_user=False):
    """Parse one transcript into a list of task-summaries (one per kept segment).

    A segment with no assistant usage is dropped (mirrors load's assistant_turns>0 filter).
    Session ids are suffixed #1/#2/... only when more than one segment is kept.
    """
    entries = _read_entries(path)
    base = os.path.splitext(os.path.basename(path))[0]
    project = os.path.basename(os.path.dirname(path))
    segs = segment_entries(entries, gap_min=gap_min, split_on_user=split_on_user)
    summaries = [reduce_entries(seg, base, project) for seg in segs]
    kept = [s for s in summaries if s["assistant_turns"] > 0]
    if len(kept) > 1:
        for i, s in enumerate(kept, 1):
            s["session"] = f"{base}#{i}"
    return kept


def load(root):
    paths = _glob_jsonl(root)
    sessions = []
    for p in paths:
        try:
            sess = parse_session(p)
            if sess["assistant_turns"] > 0:
                sessions.append(sess)
        except Exception as ex:
            print(f"  ! skipped {p}: {ex}", file=sys.stderr)
    return sessions


def _glob_jsonl(root):
    if os.path.isfile(root) and root.endswith(".jsonl"):
        return [root]
    return glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)


def load_segmented(root, gap_min=30, split_on_user=False):
    """Like load(), but split each transcript into task-sized segments first (ROADMAP #2)."""
    sessions = []
    for p in _glob_jsonl(root):
        try:
            for seg in parse_session_segments(p, gap_min=gap_min, split_on_user=split_on_user):
                if seg["assistant_turns"] > 0:
                    sessions.append(seg)
        except Exception as ex:
            print(f"  ! skipped {p}: {ex}", file=sys.stderr)
    return sessions


# --------------------------------------------------------------------------------------
# Multi-agent readers (ROADMAP #4): a reader maps ONE log file to a list of the SAME
# session-summary dicts reduce_entries produces. The forecast/report/budget layer is
# agent-agnostic; only the reader differs. New tools (Cursor, Copilot/gh, Codex, Aider)
# slot into READERS without touching anything else.
# See docs/superpowers/specs/2026-06-06-multi-agent-readers-design.md.
# --------------------------------------------------------------------------------------
DEFAULT_FORMAT = "claude-code"


def _read_claude_code(path):
    """claude-code reader == today's parser, wrapped to the list contract (0 or 1 summary)."""
    return [parse_session(path)]


def _sniff_claude_code(entries):
    """Confidence that these first-lines look like a Claude Code transcript envelope."""
    if not entries:
        return 0.0
    hits = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        msg = e.get("message")
        if e.get("type") in ("user", "assistant") and isinstance(msg, dict):
            hits += 1
        elif isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            hits += 1
    return hits / len(entries)


def _read_generic(path):
    """Reader for the documented minimal generic schema (README 'Extending it').

    One JSON object per line, one per assistant message:
      {"timestamp": ..., "model": ..., "usage": {input_tokens, output_tokens,
       cache_creation_input_tokens?, cache_read_input_tokens?},
       "tools": [...]?, "files": [...]?}
    Reduces to the same session-summary contract; cost via the shared entry_cost/price_for
    so a claude-* id prices correctly and an unknown id falls back to Sonnet (flagged).
    Never raises: returns [] on a file that isn't this shape.
    """
    try:
        raw = _read_entries(path)
    except Exception:
        return []
    base = os.path.splitext(os.path.basename(path))[0]
    project = os.path.basename(os.path.dirname(path))
    s = _new_summary(base, project)
    saw_msg = False
    for e in raw:
        if not isinstance(e, dict):
            continue
        if e.get("tokencast_accurate") is True:
            s["accurate"] = True
        usage = e.get("usage")
        if not isinstance(usage, dict):
            continue
        # A generic line must carry token usage to be a message; otherwise skip it.
        if not any(k in usage for k in ("input_tokens", "output_tokens",
                                        "cache_read_input_tokens",
                                        "cache_creation_input_tokens")):
            continue
        saw_msg = True
        model = e.get("model")
        _track_ts(s, e.get("timestamp"))
        s["assistant_turns"] += 1
        s["input"] += _num(usage.get("input_tokens"))
        s["output"] += _num(usage.get("output_tokens"))
        s["cache_write"] += _num(usage.get("cache_creation_input_tokens"))
        s["cache_read"] += _num(usage.get("cache_read_input_tokens"))
        if model:
            s["models"].add(model)
        s["cost"] += e.get("costUSD") if _valid_cost(e.get("costUSD")) \
            else entry_cost(usage, model)
        # Same input-token undercount honesty as Claude Code: a placeholder input_tokens
        # alongside real output counts as a suspect entry.
        if _num(usage.get("output_tokens")) > 0 and _num(usage.get("input_tokens")) <= 1:
            s["undercount_hits"] += 1
        tools = e.get("tools")
        if isinstance(tools, list):
            s["tool_calls"] += len(tools)
        # Files touched: the explicit per-message "files" list is the signal (a tool name
        # alone can't identify which file, so we don't synthesize one).
        files = e.get("files")
        if isinstance(files, list):
            for fp in files:
                if fp:
                    s["files"].add(fp)
    if not saw_msg:
        return []
    s["files_touched"] = len(s["files"])
    _finalize_duration(s)
    return [s]


def _sniff_generic(entries):
    """Confidence that these first-lines are flat generic-schema messages (model + usage,
    no Claude Code 'message' envelope)."""
    if not entries:
        return 0.0
    hits = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        if "message" in e:  # that's the Claude Code envelope, not generic
            continue
        if isinstance(e.get("usage"), dict) and ("model" in e):
            hits += 1
    return hits / len(entries)


# format name -> (reader, sniffer). Add a tool here to support it; nothing else changes.
READERS = {
    "claude-code": (_read_claude_code, _sniff_claude_code),
    "generic": (_read_generic, _sniff_generic),
}


def _sniff_format(path, n=8):
    """Pick the best-scoring reader for one file; default to claude-code on a tie/low score."""
    try:
        entries = _read_entries(path)[:n]
    except Exception:
        return DEFAULT_FORMAT
    best, best_score = DEFAULT_FORMAT, 0.0
    for name, (_read, sniff) in READERS.items():
        try:
            score = sniff(entries)
        except Exception:
            score = 0.0
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= 0.5 else DEFAULT_FORMAT


def read_file(path, fmt="auto"):
    """Read ONE log file into session summaries using the chosen (or auto-detected) reader.

    Never raises on a foreign/empty file: returns []. The loader filters assistant_turns==0.
    """
    name = _sniff_format(path) if fmt == "auto" else fmt
    reader = READERS.get(name)
    if reader is None:
        return []
    try:
        return reader[0](path) or []
    except Exception as ex:
        print(f"  ! skipped {path}: {ex}", file=sys.stderr)
        return []


def load_with_format(root, fmt="auto"):
    """Like load(), but route each file through the multi-agent reader registry (ROADMAP #4).

    fmt='auto' sniffs each file; an explicit format selects one reader for every file.
    With fmt='claude-code' this is equivalent to load(root).
    """
    sessions = []
    for p in _glob_jsonl(root):
        for sess in read_file(p, fmt):
            if isinstance(sess, dict) and sess.get("assistant_turns", 0) > 0:
                sessions.append(sess)
    return sessions


# --------------------------------------------------------------------------------------
# Stats helpers
# --------------------------------------------------------------------------------------
def pct(values, q):
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo = math.floor(k); hi = math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def money(x):
    return f"${x:,.2f}"


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------
def cmd_report(args):
    segment = getattr(args, "segment", False)
    fmt = getattr(args, "format", "auto")
    if segment:
        sessions = load_segmented(args.path, getattr(args, "gap_min", 30),
                                  getattr(args, "split_on_user", False))
    elif fmt and fmt != "auto":
        sessions = load_with_format(args.path, fmt)
    else:
        sessions = load(args.path)  # default/auto path == today's claude-code behavior
    if not sessions:
        print(f"No usable sessions found under {args.path}. Try `demo` first.")
        return
    total = sum(s["cost"] for s in sessions)
    costs = [s["cost"] for s in sessions]
    by_proj, by_model, by_day = defaultdict(float), defaultdict(float), defaultdict(float)
    fallback = 0
    for s in sessions:
        by_proj[s["project"]] += s["cost"]
        for m in (s["models"] or {"unknown"}):
            by_model[m] += s["cost"] / max(1, len(s["models"]))
        if s["ts_first"]:
            by_day[s["ts_first"][:10]] += s["cost"]
        if not s["models"]:
            fallback += 1

    print("=" * 68)
    print("TokenCast - spend attribution")
    print("=" * 68)
    if segment:
        gap = getattr(args, "gap_min", 30)
        print(f"Tasks analyzed    : {len(sessions)}  (sessions segmented at idle gaps >{gap} min)")
    else:
        print(f"Sessions analyzed : {len(sessions)}")
    print(f"Total spend       : {money(total)}")
    print(f"Mean / session    : {money(total/len(sessions))}")
    print(f"Per-task spread   : p50 {money(pct(costs,.5))}   "
          f"p90 {money(pct(costs,.9))}   p95 {money(pct(costs,.95))}   max {money(max(costs))}")
    print()
    print("By project:")
    for k, v in sorted(by_proj.items(), key=lambda x: -x[1])[:8]:
        print(f"  {(v / total * 100 if total else 0.0):5.1f}%  {money(v):>12}  {k}")
    print("By model:")
    for k, v in sorted(by_model.items(), key=lambda x: -x[1])[:8]:
        print(f"  {(v / total * 100 if total else 0.0):5.1f}%  {money(v):>12}  {k}")

    # The honest part: data quality.
    total_turns = sum(s["assistant_turns"] for s in sessions)
    undercount  = sum(s["undercount_hits"] for s in sessions)
    print()
    print("Data-quality check:")
    if total_turns:
        frac = undercount / total_turns
        flag = "  <-- input_tokens look like streaming placeholders" if frac > 0.2 else ""
        print(f"  {frac*100:.0f}% of assistant turns have input_tokens<=1 with output>0{flag}")
    if fallback:
        print(f"  {fallback} session(s) had no model id; priced at Sonnet fallback")
    print("  Note: Claude Code's JSONL undercounts raw input tokens (cache fields are")
    print("  reliable), so absolute costs here are a FLOOR. Accumulate accurate runs")
    print("  (tokencast-optimize run/auto) to calibrate on real token counts.")

    if args.cap is not None:
        clipped = [s for s in sessions if s["cost"] > args.cap]
        spill = sum(s["cost"] - args.cap for s in clipped)
        print()
        print(f"If a hard cap of {money(args.cap)}/session had been enforced:")
        print(f"  {len(clipped)} of {len(sessions)} tasks ({len(clipped)/len(sessions)*100:.0f}%) "
              f"would have been cut off mid-work.")
        print(f"  {money(spill)} of real work sat above the line.")

    print()
    print("Most expensive tasks:")
    for s in sorted(sessions, key=lambda x: -x["cost"])[:5]:
        print(f"  {money(s['cost']):>10}  turns={s['assistant_turns']:>3} "
              f"tools={s['tool_calls']:>3} files={s['files_touched']:>2}  {s['session'][:28]}")


def _mins(x):
    if x is None:
        return "  n/a"
    if x >= 60:
        return f"{x/60:.1f} h"
    return f"{x:.0f} min"


def _dedup_sessions(sessions):
    """One session per (project, session) key, order-preserving.

    On a key collision, prefer the accurate copy (a harness-measured run) over an
    undercounted one, keeping the first occurrence's position.
    """
    order, by_key = [], {}
    for s in sessions:
        key = (s["project"], s["session"])
        if key not in by_key:
            by_key[key] = s
            order.append(key)
        elif s.get("accurate") and not by_key[key].get("accurate"):
            by_key[key] = s
    return [by_key[k] for k in order]


def _validate_sizes(args):
    """Reject negative size inputs before they reach log1p (which crashes on < 0).

    Mirrors the inline-hint guard so the CLI flags can't sneak a negative through.
    """
    for name in ("files", "tools", "output", "count"):
        v = getattr(args, name, None)
        if v is not None and v < 0:
            raise SystemExit(f"tokencast: --{name} must be >= 0, got {v}")


# --------------------------------------------------------------------------------------
# Forecast model (ROADMAP #3): distance-weighted kNN, log-scaled skewed features,
# cache_read as a feature, adaptive k, and a neighbor-spread confidence label.
# Kept as small pure helpers so cmd_forecast's body stays minimal and merge-friendly.
# See docs/superpowers/specs/2026-06-06-forecast-model-design.md.
# --------------------------------------------------------------------------------------

# Skewed, heavy-tailed features are standardized on log1p so distance reflects
# proportional (not absolute-outlier) similarity. Bounded counts stay linear.
_LOG_FEATURES = {"output", "tool_calls", "cache_read"}


def _forecast_features():
    """The feature vector. cache_read is the real cost driver (cache-read tokens)."""
    return ["files_touched", "tool_calls", "output", "assistant_turns", "cache_read"]


def _scale(value, feature):
    """Per-feature coordinate transform: log1p for skewed features, identity otherwise."""
    v = value or 0
    return math.log1p(v) if feature in _LOG_FEATURES else float(v)


def _adaptive_k(n):
    """Neighborhood size: max(5, n // 4) -- a floor of 5 so even tiny pools forecast."""
    return max(5, n // 4)


def _neighbor_weights(distances):
    """Closer neighbors weigh more: 1 / (1 + d / median_d). Scale-free, always positive."""
    pos = [d for d in distances if d > 0]
    med = statistics.median(pos) if pos else 1.0
    if med <= 0:
        med = 1.0
    return [1.0 / (1.0 + (d / med)) for d in distances]


def _weighted_pct(values, weights, q):
    """Distance-weighted percentile. Reduces EXACTLY to pct() when all weights are equal.

    Symmetric type-7 plotting positions: position(i) uses the inclusive cumulative weight
    minus half of point i's own weight minus half the first point's weight, normalized so
    positions[0]==0 and positions[-1]==1. Unlike the old `total - weights[-1]` form, every
    weight -- including the largest point's -- shifts the interior positions, so a far
    (low-weight) neighbor is genuinely down-weighted instead of ignored. (With only two
    points there is no interior, so the result is weight-independent -- inherent to
    interpolating a percentile between exactly two samples.)
    """
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights), key=lambda vw: vw[0])
    xs = [v for v, _ in pairs]
    ws = [w for _, w in pairs]
    n = len(xs)
    if n == 1:
        return xs[0]
    total = sum(ws)
    if total <= 0:
        return pct(values, q)
    cum, run = [], 0.0
    for w in ws:
        run += w
        cum.append(run)  # inclusive cumulative weight up to and including i
    half0, halfN = ws[0] / 2.0, ws[-1] / 2.0
    denom = total - half0 - halfN
    if denom <= 0:
        return pct(values, q)
    positions = [(c - w / 2.0 - half0) / denom for c, w in zip(cum, ws)]
    if q <= positions[0]:
        return xs[0]
    if q >= positions[-1]:
        return xs[-1]
    for i in range(1, n):
        if q <= positions[i]:
            lo, hi = positions[i - 1], positions[i]
            frac = 0.0 if hi == lo else (q - lo) / (hi - lo)
            return xs[i - 1] + frac * (xs[i] - xs[i - 1])
    return xs[-1]


def _match_label(distances):
    """One-word confidence from neighbor-distance spread (coefficient of variation)."""
    if len(distances) < 2:
        return "tight"
    m = statistics.mean(distances)
    if m <= 0:
        return "tight"
    cv = statistics.pstdev(distances) / m
    if cv < 0.35:
        return "tight"
    if cv < 0.75:
        return "moderate"
    return "loose"


def _knn_forecast(sessions, target):
    """Core model. Returns (neighbors, weights, ncosts, ndurs, k, match_label).

    `target` supplies feature values; any missing feature falls back to the pool mean.
    """
    feats = _forecast_features()
    means = {f: statistics.mean(s[f] for s in sessions) for f in feats}
    # Standardize in scaled space so log-features and linear-features are comparable.
    scaled = {f: [_scale(s[f], f) for s in sessions] for f in feats}
    stds = {f: (statistics.pstdev(scaled[f]) or 1.0) for f in feats}
    tgt = {f: _scale(target.get(f, means[f]), f) for f in feats}

    def dist(s):
        return math.sqrt(sum(((_scale(s[f], f) - tgt[f]) / stds[f]) ** 2 for f in feats))

    k = _adaptive_k(len(sessions))
    ranked = sorted(sessions, key=dist)[:k]
    dists = [dist(s) for s in ranked]
    weights = _neighbor_weights(dists)
    ncosts = [s["cost"] for s in ranked]
    ndurs = [s["duration_min"] for s in ranked if s["duration_min"] is not None]
    return ranked, weights, ncosts, ndurs, k, _match_label(dists)


def cmd_forecast(args):
    _validate_sizes(args)
    segment = getattr(args, "segment", False)
    fmt = getattr(args, "format", "auto")
    runs = getattr(args, "runs", "./runs")
    if segment:
        gap = getattr(args, "gap_min", 30)
        sou = getattr(args, "split_on_user", False)
        loaded = load_segmented(args.path, gap, sou) + load_segmented(runs, gap, sou)
    elif fmt and fmt != "auto":
        # Format-specific history; runs are always TokenCast-written claude-code JSONL.
        loaded = load_with_format(args.path, fmt) + load(runs)
    else:
        loaded = load(args.path) + load(runs)  # default/auto == today's behavior
    pool = _dedup_sessions(loaded)
    accurate = [s for s in pool if s.get("accurate")]
    sessions, accurate_basis = (accurate, True) if len(accurate) >= 5 else (pool, False)
    if len(sessions) < 5:
        print("Need at least ~5 historical sessions to calibrate a forecast.")
        return
    # Distance-weighted kNN over log-scaled features incl. cache_read (the cost
    # driver). target supplies what the user passed; the rest falls back to means.
    # See _knn_forecast + the design spec.
    target = {}
    if args.files is not None:
        target["files_touched"] = args.files
    if args.tools is not None:
        target["tool_calls"] = args.tools
    if args.output is not None:
        target["output"] = args.output
    neighbors, weights, ncosts, ndurs, k, match = _knn_forecast(sessions, target)
    means = {f: statistics.mean(s[f] for s in sessions) for f in _forecast_features()}
    # Resolve the displayed target profile (mean fallback) for the header line.
    target = {f: target.get(f, means[f]) for f in _forecast_features()}
    # Weights aligned to ncosts (all neighbors) and to ndurs (neighbors w/ duration).
    cweights = list(weights)
    dweights = [w for s, w in zip(neighbors, weights) if s["duration_min"] is not None]

    print("=" * 68)
    print("TokenCast - task estimate (calibrated on YOUR history, not a guess)")
    print("=" * 68)
    print(f"Task profile: ~{target['files_touched']:.0f} files, "
          f"~{target['tool_calls']:.0f} tool calls")
    print(f"Matched against {k} most similar past tasks (of {len(sessions)}). "
          f"Neighbor fit: {match}.")
    if segment:
        gap = getattr(args, "gap_min", 30)
        extra = ", new user turns" if getattr(args, "split_on_user", False) else ""
        print(f"Sessions segmented into tasks at idle gaps >{gap} min{extra} "
              "(a 'task' ~= a planner's unit, not a whole session).")
    if accurate_basis:
        print(f"Calibrated on {len(sessions)} accurate harness runs (real token counts).")
    else:
        print("Built on Claude Code logs that undercount input tokens -- this is a FLOOR.")
        print("Accumulate accurate runs (tokencast-optimize run/auto) to calibrate.")
    print()
    print("Per task:")
    print(f"  Cost   p50 {money(_weighted_pct(ncosts,cweights,.5)):>9}   "
          f"p90 {money(_weighted_pct(ncosts,cweights,.9)):>9}   "
          f"p95 {money(_weighted_pct(ncosts,cweights,.95)):>9}")
    if ndurs:
        print(f"  Time   p50 {_mins(_weighted_pct(ndurs,dweights,.5)):>9}   "
              f"p90 {_mins(_weighted_pct(ndurs,dweights,.9)):>9}   "
              f"p95 {_mins(_weighted_pct(ndurs,dweights,.95)):>9}")
        print("         (wall-clock incl. think/idle time -- a rough timeline proxy)")
    print()
    print("  -> Put the p90 in the estimate, not the p50. The same task does not cost")
    print("     the same twice, and the next model release moves this whole curve.")

    # Sprint / project aggregate via Monte Carlo over the matched distribution.
    if args.count and args.count > 1:
        import random
        random.seed(0)
        TRIALS = 5000
        totals_c, totals_t = [], []
        for _ in range(TRIALS):
            c = sum(random.choices(ncosts, weights=cweights, k=args.count))
            totals_c.append(c)
            if ndurs:
                totals_t.append(sum(random.choices(ndurs, weights=dweights, k=args.count)))
        print()
        print(f"Sprint / project of {args.count} similar tasks (Monte Carlo, {TRIALS} trials):")
        print(f"  Budget  p50 {money(pct(totals_c,.5)):>10}   p90 {money(pct(totals_c,.9)):>10}")
        if totals_t:
            print(f"  Effort  p50 {_mins(pct(totals_t,.5)):>10}   p90 {_mins(pct(totals_t,.9)):>10}"
                  "   (sequential; parallelize across engineers to compress)")
        print("  Plan the budget and the deadline to the p90 column.")

    print()
    print("Comparable past tasks:")
    for s in sorted(neighbors, key=lambda x: -x["cost"])[:6]:
        print(f"  {money(s['cost']):>10}  {_mins(s['duration_min']):>7}  "
              f"files={s['files_touched']:>2} tools={s['tool_calls']:>3} turns={s['assistant_turns']:>3}")


# --------------------------------------------------------------------------------------
# estimate (ROADMAP #5): annotate a plan/ticket markdown file with per-ticket p90
# cost+time and a sprint total -- the literal "cost line in the plan". Reuses the SAME
# kNN forecaster and accuracy bridge as `forecast`; all helpers below are NEW + pure.
# See docs/superpowers/specs/2026-06-06-estimate-and-packaging-design.md.
# --------------------------------------------------------------------------------------

PlanTicket = namedtuple("PlanTicket", ["text", "files", "tools", "output"])

# A line is a ticket iff it starts (after indent) with a bullet (- * +, incl. GitHub
# checkboxes) or an ordered-list marker (1. / 2) ). Headings, blanks, prose are ignored.
_BULLET_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^\[[ xX]\]\s+(.*)$")
# Trailing parenthesised size hint, e.g. "(files=8 tools=30 output=4000)".
_HINT_RE = re.compile(r"\s*\(([^()]*)\)\s*$")


def _parse_size_hint(text):
    """Split a trailing '(files=.. tools=.. output=..)' hint off `text`.

    Returns (clean_text, files, tools, output). If the parenthetical isn't a valid
    hint (unknown keys / malformed), it's left in the text untouched and all sizes are
    None -- so ordinary prose parentheses never get mangled.
    """
    m = _HINT_RE.search(text)
    if not m:
        return text, None, None, None
    body = m.group(1).strip()
    if not body:
        return text, None, None, None
    sizes = {"files": None, "tools": None, "output": None}
    for tok in body.split():
        if "=" not in tok:
            return text, None, None, None  # not a hint group; leave as-is
        key, _, val = tok.partition("=")
        key = key.strip().lower()
        if key not in sizes:
            return text, None, None, None
        try:
            n = int(val.strip())
        except ValueError:
            return text, None, None, None
        if n < 0:
            return text, None, None, None  # negatives aren't a valid task size; treat as prose
        sizes[key] = n
    clean = text[: m.start()].rstrip()
    return clean, sizes["files"], sizes["tools"], sizes["output"]


def parse_plan(text):
    """Pure: parse plan markdown into a list of PlanTicket.

    Each bullet / checkbox / ordered-list line is a ticket; headings, blanks, and prose
    are ignored. A trailing '(files=.. tools=.. output=..)' hint is parsed off and removed
    from the ticket text. See the module-level rule + the design spec.
    """
    tickets = []
    for raw in text.splitlines():
        m = _BULLET_RE.match(raw)
        if not m:
            continue
        body = m.group(2).strip()
        cb = _CHECKBOX_RE.match(body)
        if cb:
            body = cb.group(1).strip()
        body, files, tools, output = _parse_size_hint(body)
        body = body.strip()
        if not body:
            continue
        tickets.append(PlanTicket(body, files, tools, output))
    return tickets


def _estimate_pool(args):
    """Build the calibration pool exactly like cmd_forecast: history + runs, deduped,
    preferring the accurate subset when >=5 exist. Returns (sessions, accurate_basis).
    """
    if getattr(args, "segment", False):
        gap = getattr(args, "gap_min", 30)
        sou = getattr(args, "split_on_user", False)
        loaded = load_segmented(args.path, gap, sou) + \
            load_segmented(getattr(args, "runs", "./runs"), gap, sou)
    else:
        loaded = load(args.path) + load(getattr(args, "runs", "./runs"))
    pool = _dedup_sessions(loaded)
    accurate = [s for s in pool if s.get("accurate")]
    return (accurate, True) if len(accurate) >= 5 else (pool, False)


def cmd_estimate(args):
    _validate_sizes(args)
    try:
        with open(args.plan, encoding="utf-8") as fh:
            tickets = parse_plan(fh.read())
    except OSError as ex:
        raise SystemExit(f"tokencast: cannot read plan file {args.plan}: {ex}")
    if not tickets:
        print(f"No tickets found in {args.plan}. Expected markdown bullets "
              "(- / * / +, incl. [ ] checkboxes) or an ordered list (1. / 2)).")
        return

    sessions, accurate_basis = _estimate_pool(args)
    if len(sessions) < 5:
        print("Need at least ~5 historical sessions to calibrate a forecast.")
        return

    print("=" * 68)
    print("TokenCast - plan estimate (p90 cost + time per ticket)")
    print("=" * 68)
    if accurate_basis:
        print(f"Basis: calibrated on {len(sessions)} accurate harness runs (real token counts).")
    else:
        print("Basis: Claude Code logs that undercount input tokens -- these are a FLOOR.")
        print("       Accumulate accurate runs (tokencast-optimize run/auto) to calibrate.")
    if getattr(args, "segment", False):
        gap = getattr(args, "gap_min", 30)
        extra = ", new user turns" if getattr(args, "split_on_user", False) else ""
        print(f"Sessions segmented into tasks at idle gaps >{gap} min{extra}.")
    print(f"Matched each of {len(tickets)} tickets against its nearest past tasks (of "
          f"{len(sessions)}).")
    print()

    sum_cost = 0.0
    sum_time = 0.0
    have_time = False
    ks = []
    # Accumulate the matched neighbour pool (cost, time, weight) across all tickets for a
    # Monte-Carlo sprint roll-up consistent with `forecast`.
    mc_costs, mc_cweights, mc_durs, mc_dweights = [], [], [], []
    for t in tickets:
        target = {}
        files = t.files if t.files is not None else args.files
        tools = t.tools if t.tools is not None else args.tools
        output = t.output if t.output is not None else args.output
        if files is not None:
            target["files_touched"] = files
        if tools is not None:
            target["tool_calls"] = tools
        if output is not None:
            target["output"] = output
        neighbors, weights, ncosts, ndurs, k, _match = _knn_forecast(sessions, target)
        ks.append(k)
        cweights = list(weights)
        dweights = [w for s, w in zip(neighbors, weights) if s["duration_min"] is not None]
        p90c = _weighted_pct(ncosts, cweights, .9)
        sum_cost += p90c
        mc_costs += ncosts
        mc_cweights += cweights
        if ndurs:
            have_time = True
            p90t = _weighted_pct(ndurs, dweights, .9)
            sum_time += p90t
            mc_durs += ndurs
            mc_dweights += dweights
            time_s = _mins(p90t)
        else:
            time_s = "  n/a"
        hint = ""
        if t.files is not None or t.tools is not None or t.output is not None:
            bits = []
            if t.files is not None:
                bits.append(f"files={t.files}")
            if t.tools is not None:
                bits.append(f"tools={t.tools}")
            if t.output is not None:
                bits.append(f"output={t.output}")
            hint = "  [" + " ".join(bits) + "]"
        print(f"  p90 {money(p90c):>9}   p90 {time_s:>8}   {t.text}{hint}")

    print()
    time_str = f"   ~{_mins(sum_time)}" if have_time else ""
    print(f"Sprint total (sum of per-ticket p90s): {money(sum_cost)}{time_str}  (sequential)")

    # Monte-Carlo roll-up over the union of matched draws, len(tickets) draws per trial.
    if len(tickets) > 1 and mc_costs:
        random.seed(0)
        TRIALS = 5000
        totals_c, totals_t = [], []
        for _ in range(TRIALS):
            totals_c.append(sum(random.choices(mc_costs, weights=mc_cweights, k=len(tickets))))
            if mc_durs:
                totals_t.append(sum(random.choices(mc_durs, weights=mc_dweights, k=len(tickets))))
        line = (f"Monte-Carlo total ({TRIALS} trials): "
                f"p50 {money(pct(totals_c,.5))}   p90 {money(pct(totals_c,.9))}")
        if totals_t:
            line += f"   (time p90 {_mins(pct(totals_t,.9))}, sequential)"
        print(line)

    hintless = sum(1 for t in tickets
                   if t.files is None and t.tools is None and t.output is None)
    if hintless >= 2:
        print()
        print(f"  Note: {hintless} tickets had no size hint, so they share one estimate")
        print("        (your history mean). Add inline (files=.. tools=..) to size them apart.")

    print()
    print("  -> Put the p90 in the plan, not the p50. Parallelize across engineers to")
    print("     compress the timeline; the same task does not cost the same twice.")


def cmd_budget(args):
    import budget  # lazy import avoids a tokencast<->budget cycle
    import datetime
    cfg = budget.BudgetConfig.load(args.config)
    if cfg is None:
        print(f"No budget configured. Create {args.config} to track spend against a cap "
              "(see README).")
        return
    records = budget.collect_spend(args.logs, args.runs)
    try:
        st = budget.status(cfg, args.scope, records, datetime.date.today())
    except ValueError as e:
        raise SystemExit(f"tokencast: {e}")

    print("=" * 68)
    print(f"TokenCast - budget ({st.scope})")
    print("=" * 68)
    print(f"Period      : {st.period_start} -> {st.period_end}  ({cfg.period})")
    print(f"Cap         : {money(st.amount)}")
    print(f"Consumed    : {money(st.consumed_total)}   "
          f"(real {money(st.consumed_real)} [floor] + tokencast {money(st.consumed_tokencast)})")
    print(f"Remaining   : {money(st.remaining)}")
    print(f"Burn rate   : {money(st.burn_rate_per_day)}/day")
    if st.runway_days is None:
        print("Runway      : no spend yet this period")
    else:
        exh = st.projected_exhaustion
        exh_s = exh.isoformat() if exh else "after period end (won't exhaust this period)"
        print(f"Runway      : {st.runway_days:.0f} days  (projected exhaustion: {exh_s})")
    if args.per_task is not None:
        print(f"Runway/task : ~{budget.runway_tasks(st.remaining, args.per_task)} tasks "
              f"at {money(args.per_task)}/task")
    if args.forecast is not None:
        verdict = "FITS" if budget.fits(st.remaining, args.forecast) else "does NOT fit"
        print(f"Forecast    : a sprint of {money(args.forecast)} {verdict} the remaining "
              f"{money(st.remaining)}")
    print()
    print("Note: real-usage spend uses Claude Code's JSONL, which undercounts input tokens,")
    print("so consumed-real is a FLOOR -- you may have less runway than shown.")


def cmd_demo(args):
    """Generate synthetic JSONL that mimics Claude Code's schema (incl. the input-token bug)."""
    random.seed(args.seed)
    out = args.out
    models = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
    os.makedirs(os.path.join(out, "demo-project"), exist_ok=True)
    for i in range(args.sessions):
        size = random.choice(["s", "s", "m", "m", "m", "l", "xl"])
        turns = {"s": (2, 6), "m": (8, 20), "l": (25, 50), "xl": (60, 120)}[size]
        n = random.randint(*turns)
        model = random.choices(models, weights=[0.25, 0.65, 0.10])[0]
        path = os.path.join(out, "demo-project", f"sess-{size}-{i:03d}.jsonl")
        with open(path, "w") as fh:
            t0 = 1748000000 + i * 86400  # one session per day, room for hour-long gaps
            t = t0
            fh.write(json.dumps({"type": "user", "timestamp": _iso(t),
                                 "message": {"role": "user", "content": "..."}}) + "\n")
            for j in range(n):
                # Occasionally the engineer walks away mid-session and resumes on a new
                # task hours later -- a large idle gap that --segment splits on.
                if j > 0 and n >= 8 and random.random() < 0.12:
                    t += random.randint(45, 180) * 60  # 45-180 min idle gap
                else:
                    t += 30
                out_tok = random.randint(300, 2500)
                cache_read = random.randint(20_000, 220_000)  # the real cost driver
                cache_write = random.randint(0, 30_000)
                # Mimic the documented bug: input_tokens is usually a placeholder.
                inp = 1 if random.random() < 0.75 else random.randint(50, 1200)
                content = [{"type": "text", "text": "..."}]
                if random.random() < 0.6:
                    tool = random.choice(list(FILE_TOOLS))
                    content.append({"type": "tool_use", "name": tool,
                                    "input": {"file_path": f"src/mod_{random.randint(1, 12)}.py"}})
                rec = {"type": "assistant", "timestamp": _iso(t),
                       "message": {"role": "assistant", "model": model, "content": content,
                                   "usage": {"input_tokens": inp, "output_tokens": out_tok,
                                             "cache_creation_input_tokens": cache_write,
                                             "cache_read_input_tokens": cache_read}}}
                fh.write(json.dumps(rec) + "\n")
    print(f"Wrote {args.sessions} synthetic sessions to {out}/demo-project/")
    print(f"Now run:  python {os.path.basename(__file__)} report {out}")


def _iso(epoch):
    import datetime
    # timezone-aware UTC, then drop tzinfo to keep the legacy "...Z" suffix shape.
    # (datetime.utcfromtimestamp is deprecated as of Python 3.12.)
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(
        tzinfo=None).isoformat() + "Z"


def main():
    ap = argparse.ArgumentParser(description="TokenCast - log-based cost estimator for agentic coding")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="attribute past spend from logs")
    r.add_argument("path", nargs="?", default=os.path.expanduser("~/.claude/projects"))
    r.add_argument("--cap", type=float, default=None, help="show what a per-session hard cap would clip")
    r.add_argument("--segment", action="store_true",
                   help="split each session into task-sized units (by idle gaps) before reporting")
    r.add_argument("--gap-min", type=int, default=30,
                   help="idle-gap threshold in minutes for --segment (default 30; 0 disables the gap rule)")
    r.add_argument("--split-on-user", action="store_true",
                   help="with --segment, also split at fresh user turns between tasks")
    r.add_argument("--format", choices=sorted(READERS) + ["auto"], default="auto",
                   help="log format reader (default auto: detect Claude Code, else fall back)")
    r.add_argument("--refresh-prices", action="store_true", help="pull current prices from the live cost map")
    r.set_defaults(func=cmd_report)

    f = sub.add_parser("forecast", help="estimate a new task's cost from your history")
    f.add_argument("path", nargs="?", default=os.path.expanduser("~/.claude/projects"))
    f.add_argument("--runs", default="./runs",
                   help="accurate TokenCast run logs to prefer over the floor history")
    f.add_argument("--files", type=int, default=None, help="expected # files the task will touch")
    f.add_argument("--tools", type=int, default=None, help="expected # tool calls")
    f.add_argument("--output", type=int, default=None, help="expected output tokens (optional)")
    f.add_argument("--count", type=int, default=None, help="# of similar tasks to roll up into a sprint/project estimate")
    f.add_argument("--segment", action="store_true",
                   help="split each session into task-sized units (by idle gaps) so 'a task' "
                        "matches a planner's unit, not a whole session")
    f.add_argument("--gap-min", type=int, default=30,
                   help="idle-gap threshold in minutes for --segment (default 30; 0 disables the gap rule)")
    f.add_argument("--split-on-user", action="store_true",
                   help="with --segment, also split at fresh user turns between tasks")
    f.add_argument("--format", choices=sorted(READERS) + ["auto"], default="auto",
                   help="log format reader for history (default auto; runs/ stay claude-code)")
    f.add_argument("--refresh-prices", action="store_true", help="pull current prices from the live cost map")
    f.set_defaults(func=cmd_forecast)

    e = sub.add_parser("estimate", help="annotate a plan.md's tickets with p90 cost+time")
    e.add_argument("plan", help="plan/ticket markdown file (bullets / checkboxes / ordered list)")
    e.add_argument("path", nargs="?", default=os.path.expanduser("~/.claude/projects"),
                   help="history root (same as forecast)")
    e.add_argument("--runs", default="./runs", help="accurate TokenCast run logs")
    e.add_argument("--files", type=int, default=None,
                   help="default expected # files per ticket (inline (files=..) hints override)")
    e.add_argument("--tools", type=int, default=None,
                   help="default expected # tool calls per ticket (inline hints override)")
    e.add_argument("--output", type=int, default=None,
                   help="default expected output tokens per ticket (inline hints override)")
    e.add_argument("--segment", action="store_true",
                   help="split transcripts into task-sized units at idle gaps before matching")
    e.add_argument("--gap-min", type=int, default=30,
                   help="idle-gap minutes that start a new segment (with --segment)")
    e.add_argument("--split-on-user", action="store_true",
                   help="also split at fresh user turns (with --segment)")
    e.add_argument("--refresh-prices", action="store_true",
                   help="pull current prices from the live cost map")
    e.set_defaults(func=cmd_estimate)

    d = sub.add_parser("demo", help="generate synthetic logs to try the tool")
    d.add_argument("--out", default="./sample_logs")
    d.add_argument("--sessions", type=int, default=40)
    d.add_argument("--seed", type=int, default=7)
    d.set_defaults(func=cmd_demo)

    b = sub.add_parser("budget", help="(optional) track spend against a budget cap")
    b.add_argument("--config", default="tokencast_budget.json",
                   help="path to the budget JSON (default ./tokencast_budget.json)")
    b.add_argument("--scope", default="global", help="global | project:NAME")
    b.add_argument("--logs", default=os.path.expanduser("~/.claude/projects"),
                   help="real Claude Code logs (counts as real usage, a floor)")
    b.add_argument("--runs", default="./runs", help="TokenCast run logs (accurate)")
    b.add_argument("--per-task", type=float, default=None,
                   help="also show runway in tasks at this per-task cost")
    b.add_argument("--forecast", type=float, default=None,
                   help="also show whether a sprint of this cost fits remaining")
    b.add_argument("--refresh-prices", action="store_true",
                   help="pull current prices from the live cost map")
    b.set_defaults(func=cmd_budget)

    args = ap.parse_args()
    if getattr(args, "refresh_prices", False):
        refresh_prices()
    args.func(args)


if __name__ == "__main__":
    main()
