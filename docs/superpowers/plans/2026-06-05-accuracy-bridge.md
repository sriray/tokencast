# TokenCast Light-Tier Accuracy Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `forecast` prefer accurate harness runs over the undercounted Claude Code history — the harness stamps a `tokencast_accurate` marker, the parser reads it, and `forecast` (CLI + browser) calibrates on accurate runs when ≥5 exist, falling back to the floor with a loud caveat otherwise.

**Architecture:** `RunResult.to_jsonl` adds `tokencast_accurate: true` to each emitted line. `tokencast.py` `parse_session` tags `session["accurate"]`; `cmd_forecast` pools history + `./runs` (deduped), prefers the accurate basis, and labels which it used. `tokencast.html` mirrors the same. No model/cost-math change.

**Tech Stack:** Python 3.8+ stdlib (`tokencast.py` stays dependency-free), `optimize/result.py`, vanilla JS in `tokencast.html`, `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-05-accuracy-bridge-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. A repo security hook blocks the four-letter token e-v-a-l immediately followed by an open paren — never write it.

**Key facts:**
- `optimize/result.py` `to_jsonl` builds a `lines` list (one `{"type":"user",...}` dict + per-turn `{"type":"assistant",...}` dicts), then writes `json.dumps(ln)` per line. It raises if `len(self.model_usage) > 1` (multi-model guard) — untouched here.
- `tokencast.py` `parse_session(path)` builds a summary dict `s`, iterating each JSON line `e`. `load(root)` globs `**/*.jsonl`, parses each, keeps sessions with `assistant_turns>0`. `cmd_forecast(args)` reads `args.path`, builds the kNN forecast, prints to stdout. The `forecast` subparser has `path` (positional), `--files`, `--tools`, `--output`, `--count`, `--refresh-prices`.
- `tests/test_result.py` already imports `tokencast` and has `test_to_jsonl_bridges_to_tokencast`.
- `tokencast.html`: `parseSession(name,text,project,P)` builds `s` (line ~174); `forecast()` (line ~319) guards `SESSIONS.length<5`, computes means/stds over `SESSIONS`, kNN, and renders the result. The result is rendered by assigning to `#forecastOut` (leave that final render line exactly as-is — only change the lines noted below).

---

## File Structure

- Modify: `optimize/result.py` — `to_jsonl` stamps the marker.
- Modify: `tokencast.py` — `parse_session` tags `accurate`; add `_dedup_sessions`; rework `cmd_forecast` (pool + prefer-accurate + label); add `--runs`.
- Modify: `tokencast.html` — `parseSession` reads the marker; `forecast()` prefers accurate + labels.
- Modify tests: `tests/test_result.py`. Create: `tests/test_forecast_accuracy.py`.
- Modify docs: `README.md`, `CLAUDE.md`, `ROADMAP.md`.

Untouched: `budget.py`, the `optimize/` loop/decompose/auto modules.

---

### Task 1: the marker (`optimize/result.py`)

**Files:**
- Modify: `optimize/result.py`
- Modify: `tests/test_result.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/test_result.py`:
```python
def test_to_jsonl_stamps_accuracy_marker(tmp_path):
    import json as _json
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    path = tmp_path / "t1-baseline.jsonl"
    rr.to_jsonl(str(path))
    with open(path, encoding="utf-8") as fh:
        lines = [_json.loads(ln) for ln in fh if ln.strip()]
    assert lines, "expected at least one emitted line"
    assert all(ln.get("tokencast_accurate") is True for ln in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_result.py -k accuracy_marker -v`
Expected: FAIL — lines have no `tokencast_accurate` key.

- [ ] **Step 3: Write the implementation**

In `optimize/result.py` `to_jsonl`, the current write block is:
```python
        with open(path, "w") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
        return path
```
Change it to stamp the marker on every line first:
```python
        for ln in lines:
            ln["tokencast_accurate"] = True
        with open(path, "w") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_result.py -v`
Expected: PASS (the new test + all existing result tests).

- [ ] **Step 5: Commit**

```bash
git add optimize/result.py tests/test_result.py
git commit -m "feat(optimize): stamp tokencast_accurate marker in to_jsonl output"
```

---

### Task 2: parser tags accuracy (`tokencast.py` `parse_session`)

**Files:**
- Modify: `tokencast.py`
- Create: `tests/test_forecast_accuracy.py`
- Modify: `tests/test_result.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_forecast_accuracy.py`:
```python
import json
import os
import types

import tokencast


def _write(path, marked, out=500, files=("a.py",)):
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}
    if marked:
        line["tokencast_accurate"] = True
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _write_many(d, n, marked):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        _write(os.path.join(d, f"s{i}.jsonl"), marked, out=500 + i * 10)


def test_parse_session_marker_sets_accurate(tmp_path):
    p = tmp_path / "m.jsonl"
    _write(str(p), marked=True)
    assert tokencast.parse_session(str(p))["accurate"] is True


def test_parse_session_no_marker_is_floor(tmp_path):
    p = tmp_path / "u.jsonl"
    _write(str(p), marked=False)
    assert tokencast.parse_session(str(p))["accurate"] is False
```

Also APPEND to the END of `test_to_jsonl_bridges_to_tokencast` in `tests/test_result.py` (a harness-written session must now read back accurate):
```python
    assert sess["accurate"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_forecast_accuracy.py tests/test_result.py -k "accurate or bridges" -v`
Expected: FAIL — `parse_session` doesn't set `accurate`.

- [ ] **Step 3: Write the implementation**

In `tokencast.py` `parse_session`, add `"accurate": False` to the summary dict `s` — the current line is:
```python
        "models": set(), "undercount_hits": 0, "ts_first": None, "ts_last": None,
```
change it to:
```python
        "models": set(), "undercount_hits": 0, "accurate": False, "ts_first": None, "ts_last": None,
```
Then, inside the per-line loop, right after the `try/except` that parses `e` (i.e. after `e = json.loads(line)` succeeds), add:
```python
            if e.get("tokencast_accurate") is True:
                s["accurate"] = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_forecast_accuracy.py tests/test_result.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tokencast.py tests/test_forecast_accuracy.py tests/test_result.py
git commit -m "feat: parse_session tags session accuracy from the tokencast_accurate marker"
```

---

### Task 3: `forecast` prefers accurate runs (`tokencast.py`)

**Files:**
- Modify: `tokencast.py`
- Modify: `tests/test_forecast_accuracy.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_forecast_accuracy.py`:
```python
def _args(path, runs, **kw):
    base = dict(path=path, runs=runs, files=8, tools=30, output=None, count=None,
                refresh_prices=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_dedup_sessions_collapses_by_project_session():
    s1 = {"project": "p", "session": "a"}
    s2 = {"project": "p", "session": "a"}
    s3 = {"project": "p", "session": "b"}
    out = tokencast._dedup_sessions([s1, s2, s3])
    assert [s["session"] for s in out] == ["a", "b"]


def test_forecast_prefers_accurate(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_many(str(runs), 6, marked=True)
    hist = tmp_path / "hist"
    os.makedirs(hist)
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "Calibrated on" in out
    assert "FLOOR" not in out


def test_forecast_floor_when_no_accurate(tmp_path, capsys):
    hist = tmp_path / "hist"
    _write_many(str(hist), 6, marked=False)
    runs = tmp_path / "none"   # does not exist
    tokencast.cmd_forecast(_args(str(hist), str(runs)))
    out = capsys.readouterr().out
    assert "FLOOR" in out
    assert "Calibrated on" not in out


def test_forecast_dedups_when_path_equals_runs(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_many(str(runs), 6, marked=True)
    tokencast.cmd_forecast(_args(str(runs), str(runs)))   # path == runs
    out = capsys.readouterr().out
    assert "(of 6)" in out      # 6 unique accurate sessions, not 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_forecast_accuracy.py -k "dedup or forecast" -v`
Expected: FAIL — no `_dedup_sessions`; `cmd_forecast` doesn't accept `runs` / print the labels.

- [ ] **Step 3: Write the implementation**

In `tokencast.py`, add the helper just above `def cmd_forecast(args):`:
```python
def _dedup_sessions(sessions):
    """Keep the first session per (project, session) key, order-preserving."""
    seen, out = set(), []
    for s in sessions:
        key = (s["project"], s["session"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
```

Replace the opening of `cmd_forecast` — currently:
```python
def cmd_forecast(args):
    sessions = load(args.path)
    if len(sessions) < 5:
        print("Need at least ~5 historical sessions to calibrate a forecast.")
        return
```
with (pool both roots, dedup, prefer accurate):
```python
def cmd_forecast(args):
    pool = _dedup_sessions(load(args.path) + load(getattr(args, "runs", "./runs")))
    accurate = [s for s in pool if s.get("accurate")]
    sessions, accurate_basis = (accurate, True) if len(accurate) >= 5 else (pool, False)
    if len(sessions) < 5:
        print("Need at least ~5 historical sessions to calibrate a forecast.")
        return
```

Add the basis label. The current block is:
```python
    print(f"Matched against {k} most similar past tasks (of {len(sessions)}).")
    print()
```
change it to:
```python
    print(f"Matched against {k} most similar past tasks (of {len(sessions)}).")
    if accurate_basis:
        print(f"Calibrated on {len(sessions)} accurate harness runs (real token counts).")
    else:
        print("Built on Claude Code logs that undercount input tokens -- this is a FLOOR.")
        print("Accumulate accurate runs (tokencast-optimize run/auto) to calibrate.")
    print()
```

Add `--runs` to the `forecast` subparser, after the `path` line:
```python
    f.add_argument("--runs", default="./runs",
                   help="accurate TokenCast run logs to prefer over the floor history")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_forecast_accuracy.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 6: Commit**

```bash
git add tokencast.py tests/test_forecast_accuracy.py
git commit -m "feat: forecast prefers accurate runs (--runs, deduped pool, honest labeling)"
```

---

### Task 4: browser mirror (`tokencast.html`)

These are surgical edits to existing functions. Leave the final result-render line in `forecast()` exactly as-is; only change the lines below.

- [ ] **Step 1: Tag accuracy in `parseSession`**

In the `parseSession` summary init (line ~174), change:
```javascript
           undercount:0, day:null, tsFirst:null, tsLast:null};
```
to:
```javascript
           undercount:0, accurate:false, day:null, tsFirst:null, tsLast:null};
```
Then, inside the `for(const line of text.split("\n"))` loop, the line that parses each entry is:
```javascript
    let e; try{ e=JSON.parse(t);}catch(_){ continue; }
```
Immediately after it, add:
```javascript
    if(e.tokencast_accurate===true) s.accurate=true;
```

- [ ] **Step 2: Choose an accurate basis in `forecast()`**

Change the function opening — currently:
```javascript
function forecast(){
  if(SESSIONS.length<5){ document.getElementById('forecastOut').innerHTML=
```
to (insert the basis computation, and guard on `basis`):
```javascript
function forecast(){
  const accurate=SESSIONS.filter(s=>s.accurate);
  const accurateBasis=accurate.length>=5;
  const basis=accurateBasis?accurate:SESSIONS;
  if(basis.length<5){ document.getElementById('forecastOut').innerHTML=
```

- [ ] **Step 3: Point the kNN at `basis`**

Make these three one-token replacements in `forecast()` (all currently reference `SESSIONS`):
1. `feats.forEach(f=>{ const a=SESSIONS.map(s=>s[f]);` → `feats.forEach(f=>{ const a=basis.map(s=>s[f]);`
2. `const k=Math.max(5,Math.floor(SESSIONS.length/4));` → `const k=Math.max(5,Math.floor(basis.length/4));`
3. `const nearObj=[...SESSIONS].sort((a,b)=>dist(a)-dist(b)).slice(0,k);` → `const nearObj=[...basis].sort((a,b)=>dist(a)-dist(b)).slice(0,k);`

- [ ] **Step 4: Append a basis label**

The line that adds the closing muted note is:
```javascript
  html += `<span class="muted">Put the p90 in your estimate. Matched on your ${k} most similar past tasks.</span>`;
```
Immediately after it, add:
```javascript
  html += accurateBasis
    ? `<br><span class="muted" style="font-size:12px;">Calibrated on ${basis.length} accurate runs (real token counts).</span>`
    : `<br><span class="muted" style="font-size:12px;">Floor: built on logs that undercount input tokens — accumulate accurate runs to calibrate.</span>`;
```

- [ ] **Step 5: Sanity-check the file still parses**

Run: `python3.11 -c "t=open('tokencast.html').read(); assert t.count('tokencast_accurate')>=1; assert 'accurateBasis' in t; assert t.count('function forecast()')==1; assert 'SESSIONS.map' not in t and '...SESSIONS]' not in t; print('ok')"`
Expected: prints `ok` (marker read, new logic present, exactly one forecast function, no leftover `SESSIONS` references in the kNN).

- [ ] **Step 6: Manual confirmation**

Open `tokencast.html`, click **Load demo data** → the forecast panel renders and shows the "Floor:" label (demo data is unmarked). Visual check; this single-file tool has no JS test harness.

- [ ] **Step 7: Commit**

```bash
git add tokencast.html
git commit -m "feat(html): forecast panel prefers accurate runs + labels the basis"
```

---

### Task 5: docs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `ROADMAP.md`

- [ ] **Step 1: Update `README.md`**

READ `README.md`. In **"The honest part (and the whole argument)"** section, after the paragraph that ends "...the tool flags this loudly in its output.", insert:
```markdown
Once you run tasks through the optimizer tier (`tokencast-optimize run`/`auto`), those runs are
measured accurately and written to `./runs` with an accuracy marker. `forecast` then **prefers
them**: with ≥5 accurate runs it calibrates on real token counts and drops the floor caveat (point
it at a different dir with `--runs DIR`). Until then it's still a floor — and says so.
```

- [ ] **Step 2: Update `ROADMAP.md`**

READ `ROADMAP.md`. Under `## 1. Fix token accuracy (the blocker)`, after the existing front-door (sub-project 6) paragraph, add:
```markdown
**Accuracy bridge — done (light tier).** The harness stamps a `tokencast_accurate` marker into the
JSONL it writes; `forecast` now pools history + `./runs`, prefers the accurate runs when ≥5 exist
(calibrating on real token counts and dropping the floor caveat), and labels which basis it used —
in both `tokencast.py` and `tokencast.html`. The remaining accuracy path is the provider Usage &
Cost API for pure Claude-Code-log forecasts.
```

- [ ] **Step 3: Update `CLAUDE.md`**

READ `CLAUDE.md`. In **"The single most important caveat (and the top roadmap item)"** section, after the existing paragraph, add:
```markdown
**Partially addressed:** the optimizer tier measures runs accurately and stamps a
`tokencast_accurate` marker into its JSONL; `forecast` (CLI + HTML) now prefers those accurate runs
(≥5 → calibrated, floor caveat dropped) over the undercounted history, deduping the pool and
labeling the basis. Pure Claude-Code-log forecasts remain a floor until a provider token API lands.
```

- [ ] **Step 4: Run the full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (6 skipped).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md ROADMAP.md
git commit -m "docs: document the light-tier accuracy bridge (ROADMAP #1)"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `RunResult.to_jsonl` stamps `tokencast_accurate: true` on every line; `parse_session` sets `session["accurate"]` from it; unmarked Claude Code logs are floor.
- `forecast` pools `path` + `--runs` (deduped by `(project, session)`), forecasts on the accurate basis when ≥5 accurate sessions exist (printing "Calibrated on N accurate harness runs") and otherwise on the floor pool (printing the FLOOR caveat).
- `tokencast.html` mirrors the prefer-accurate behavior + label; the file still has exactly one `forecast()` and renders demo data as floor.
- `tokencast.py` stays stdlib-only; the heavy-tier cost math is unchanged; `budget.py` and the optimize/decompose/auto modules are untouched.
