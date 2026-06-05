# TokenCast Skills/MCP Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the deferred skills wiring so a config's skills actually apply at run time — a `skills` name allow-list (→ the SDK `skills` option) and dir-staging (`skills_source` → the run's sandbox `cwd/.claude/skills`) — and add `available_skills`/`available_mcp` catalog helpers for the 4b-ii generator. Pure plumbing: no LLM, no `optimize` CLI change.

**Architecture:** `AgentConfig` gains a `skills` list mapped (with `setting_sources`) in `to_sdk_options`; a new `optimize/staging.py` copies a config's `skills_source` into the sandbox cwd; `run_evalset` calls it before the run; `optimize/catalog.py` enumerates available skills/MCP. All stdlib, testable without the SDK.

**Tech Stack:** Python 3.8+ (`os`, `shutil`, `json`), reuses `AgentConfig`/`run_evalset`/sandbox. No `claude-agent-sdk` (the wiring only produces option dicts + stages files). `pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-skills-mcp-wiring-design.md`

**Environment note:** use `python3.11 -m pytest ...`. End commit messages with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

**Key facts:**
- `AgentConfig` (optimize/config.py) fields: `config_id, model, system_prompt_append, allowed_tools, disallowed_tools, mcp_servers, skills_source, budget_usd, max_turns`. `load` sets `skills_source` to the `skills/` subdir path if present; `to_sdk_options` returns a plain dict; `save` writes metadata.yaml/instructions.md/tools.json. `config.py` imports `json`, `os`, dataclass/typing, a `yaml` shim.
- `run_evalset(evalset, config, *, runner=None, judge=None, out_dir="runs")` in `optimize/evalrun.py` runs each task in a `task_sandbox(task)` cwd.

---

## File Structure

- Modify: `optimize/config.py` — add `skills` field; `load`/`save` it; map `skills` + `setting_sources` in `to_sdk_options`.
- Create: `optimize/staging.py` — `stage_skills(config, cwd)`.
- Create: `optimize/catalog.py` — `available_skills`, `available_mcp`.
- Modify: `optimize/evalrun.py` — call `stage_skills` inside the sandbox before the run.
- Modify tests: `tests/test_config.py`, `tests/test_config_save.py`, `tests/test_evalrun.py`. Create: `tests/test_staging.py`, `tests/test_catalog.py`.
- Modify docs: `ROADMAP.md`, `CLAUDE.md`, `README.md`.

Untouched: `tokencast.py`, `tokencast.html`, `budget.py`.

---

### Task 1: `AgentConfig.skills` + `to_sdk_options` mapping

**Files:**
- Modify: `optimize/config.py`
- Modify: `tests/test_config.py`, `tests/test_config_save.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_config.py`:
```python
def test_to_sdk_options_maps_skills(tmp_path):
    cfg_dir = tmp_path / "withskills"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "sonnet", "skills": ["pdf", "docx"]}))
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert opts["skills"] == ["pdf", "docx"]
    assert opts["setting_sources"] == ["user", "project"]


def test_to_sdk_options_no_skills_no_setting_sources(tmp_path):
    cfg_dir = tmp_path / "plain"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert "skills" not in opts
    assert "setting_sources" not in opts   # no skills, no skills/ dir


def test_load_rejects_non_list_skills(tmp_path):
    cfg_dir = tmp_path / "bad"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "sonnet", "skills": "pdf"}))
    with pytest.raises(ValueError, match="skills"):
        AgentConfig.load(str(cfg_dir))
```

Then UPDATE the existing `test_to_sdk_options_maps_fields` in `tests/test_config.py` — its fixture (`_write_config`) creates a `skills/` subdir, so `skills_source` is set and `to_sdk_options` now emits `setting_sources`. Replace its final two lines:
```python
    # Skills staging is finalized in a later sub-project; not mapped to SDK options yet.
    assert "setting_sources" not in opts
```
with:
```python
    # 4b-i: a config dir with a skills/ subdir now maps skills_source -> setting_sources.
    assert opts["setting_sources"] == ["user", "project"]
    assert "skills" not in opts   # no `skills` name-list in metadata for this fixture
```

APPEND to `tests/test_config_save.py`:
```python
def test_save_roundtrips_skills(tmp_path):
    cfg = AgentConfig(config_id="c", model="sonnet", skills=["pdf", "docx"])
    cfg.save(str(tmp_path / "c"))
    back = AgentConfig.load(str(tmp_path / "c"))
    assert back.skills == ["pdf", "docx"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/test_config.py -k "skills or maps_fields" tests/test_config_save.py -k skills -v`
Expected: FAIL — `AgentConfig` has no `skills` field; `to_sdk_options` doesn't emit `skills`/`setting_sources`.

- [ ] **Step 3: Write minimal implementation**

In `optimize/config.py`:

(a) Add the field to the `AgentConfig` dataclass, after `skills_source`:
```python
    skills_source: Optional[str] = None
    skills: Optional[List[str]] = None
    budget_usd: Optional[float] = None
    max_turns: Optional[int] = None
```
(Keep `budget_usd`/`max_turns` after it; all have defaults so order is fine.)

(b) In `load`, after the existing `tools`/`skills_dir` handling and before the `return cls(...)`, validate and read `skills` from `meta`:
```python
        skills = meta.get("skills")
        if skills is not None and not (
                isinstance(skills, list) and all(isinstance(s, str) for s in skills)):
            raise ValueError(f"{meta_path}: 'skills' must be a list of strings")
```
and add `skills=skills,` to the `return cls(...)` kwargs (next to `skills_source=skills_source,`).

(c) In `save`, in the `meta` dict construction (where `config_id`/`model`/`budget_usd`/`max_turns` are added), also add:
```python
        if self.skills is not None:
            meta["skills"] = list(self.skills)
```

(d) In `to_sdk_options`, replace the trailing skills NOTE comment block:
```python
        # NOTE: skills_source is intentionally NOT mapped to SDK options yet. The exact
        # filesystem-staging mechanism is finalized in the skills-optimization sub-project.
        return opts
```
with:
```python
        if self.skills is not None:
            opts["skills"] = list(self.skills)
        # Skills need a discovery source: "project" finds the staged sandbox skills,
        # "user" finds the engineer's ~/.claude/skills. Only set when skills are in play.
        if self.skills is not None or self.skills_source:
            opts["setting_sources"] = ["user", "project"]
        return opts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/test_config.py tests/test_config_save.py -v`
Expected: PASS (new skills tests + the updated maps_fields test + all prior config tests)

- [ ] **Step 5: Commit**

```bash
git add optimize/config.py tests/test_config.py tests/test_config_save.py
git commit -m "feat(optimize): AgentConfig.skills -> SDK skills option + setting_sources"
```

---

### Task 2: `stage_skills` (`optimize/staging.py`)

**Files:**
- Create: `optimize/staging.py`
- Test: `tests/test_staging.py`

- [ ] **Step 1: Write the failing test**

`tests/test_staging.py`:
```python
import os

from optimize.config import AgentConfig
from optimize.staging import stage_skills


def test_stage_skills_copies_into_cwd(tmp_path):
    src = tmp_path / "cfgskills"
    (src / "myskill").mkdir(parents=True)
    (src / "myskill" / "SKILL.md").write_text("---\ndescription: x\n---\n")
    (src / "myskill" / "helper.py").write_text("x = 1\n")
    cwd = tmp_path / "sandbox"
    cwd.mkdir()

    cfg = AgentConfig(config_id="c", model="sonnet", skills_source=str(src))
    stage_skills(cfg, str(cwd))

    assert os.path.isfile(os.path.join(str(cwd), ".claude", "skills", "myskill", "SKILL.md"))
    assert os.path.isfile(os.path.join(str(cwd), ".claude", "skills", "myskill", "helper.py"))


def test_stage_skills_noop_without_source(tmp_path):
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    stage_skills(AgentConfig(config_id="c", model="sonnet"), str(cwd))   # skills_source None
    assert not os.path.exists(os.path.join(str(cwd), ".claude"))
    # non-existent skills_source is also a no-op
    stage_skills(AgentConfig(config_id="c", model="sonnet", skills_source="/no/such/dir"),
                 str(cwd))
    assert not os.path.exists(os.path.join(str(cwd), ".claude"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_staging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.staging'`

- [ ] **Step 3: Write minimal implementation**

`optimize/staging.py`:
```python
"""Stage a config's skills_source dir into a run's sandbox cwd so the Agent SDK can discover
them as project skills (cwd/.claude/skills). Pure filesystem; never touches the SDK."""
import os
import shutil


def stage_skills(config, cwd):
    """Copy config.skills_source/* into cwd/.claude/skills/. No-op if skills_source is unset
    or not a directory."""
    src = config.skills_source
    if not src or not os.path.isdir(src):
        return
    dest = os.path.join(cwd, ".claude", "skills")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dest, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_staging.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/staging.py tests/test_staging.py
git commit -m "feat(optimize): stage_skills copies a config's skills into the sandbox cwd"
```

---

### Task 3: catalogs (`optimize/catalog.py`)

**Files:**
- Create: `optimize/catalog.py`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: Write the failing test**

`tests/test_catalog.py`:
```python
import json

from optimize.catalog import available_skills, available_mcp


def test_available_skills_lists_dirs_with_skill_md(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "SKILL.md").write_text("---\ndescription: a\n---\n")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "SKILL.md").write_text("---\ndescription: b\n---\n")
    (tmp_path / "notaskill").mkdir()   # no SKILL.md -> excluded
    assert available_skills(str(tmp_path)) == ["alpha", "beta"]


def test_available_skills_missing_dir_is_empty():
    assert available_skills("/no/such/skills/dir") == []


def test_available_mcp_bare_map(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"pw": {"command": "npx"}, "fs": {"command": "node"}}))
    assert available_mcp(str(p)) == {"pw": {"command": "npx"}, "fs": {"command": "node"}}


def test_available_mcp_wrapper(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"pw": {"command": "npx"}}}))
    assert available_mcp(str(p)) == {"pw": {"command": "npx"}}


def test_available_mcp_missing_or_malformed(tmp_path):
    assert available_mcp("/no/such/file.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json ")
    assert available_mcp(str(bad)) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.catalog'`

- [ ] **Step 3: Write minimal implementation**

`optimize/catalog.py`:
```python
"""Catalogs of available skills + MCP servers for the failure-driven generator (sub-project
4b-ii). Pure stdlib; never raises on missing/malformed input (degrades to empty)."""
import json
import os


def available_skills(skills_dir=None):
    """Sorted names of skill dirs (those containing a SKILL.md). Defaults to ~/.claude/skills."""
    skills_dir = skills_dir or os.path.expanduser("~/.claude/skills")
    if not os.path.isdir(skills_dir):
        return []
    return sorted(
        name for name in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md")))


def available_mcp(catalog_path=None):
    """{server_name: server_config}. With an explicit catalog_path, accepts a bare {name: cfg}
    map or a {"mcpServers": {...}} wrapper. With no path, reads ~/.claude.json's mcpServers.
    Missing/malformed -> {}."""
    path = catalog_path or os.path.expanduser("~/.claude.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    if catalog_path:
        return data   # an explicit catalog file may be a bare {name: cfg} map
    return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_catalog.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/catalog.py tests/test_catalog.py
git commit -m "feat(optimize): available_skills + available_mcp catalogs"
```

---

### Task 4: stage skills in `run_evalset`

**Files:**
- Modify: `optimize/evalrun.py`
- Modify: `tests/test_evalrun.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/test_evalrun.py`:
```python
def test_run_evalset_stages_skills(tmp_path):
    cfg_dir = tmp_path / "baseline"
    (cfg_dir / "skills" / "myskill").mkdir(parents=True)
    (cfg_dir / "skills" / "myskill" / "SKILL.md").write_text("---\ndescription: x\n---\n")
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    config = AgentConfig.load(str(cfg_dir))

    seen = {}

    def fake_runner(prompt, options, cwd):
        seen["staged"] = os.path.isfile(
            os.path.join(cwd, ".claude", "skills", "myskill", "SKILL.md"))
        return {"turns": [], "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}},
            "num_turns": 1, "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "x"}}

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "t", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]})
    run_evalset(evalset, config, runner=fake_runner, judge=lambda p: 1.0,
                out_dir=str(tmp_path / "runs"))
    assert seen["staged"] is True   # the staged skill was present in the run's cwd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_evalrun.py -k stages_skills -v`
Expected: FAIL — `seen["staged"]` is False (skills not staged yet), so the assert fails.

- [ ] **Step 3: Write minimal implementation**

In `optimize/evalrun.py`, add the staging import near the top (after the existing `from optimize.sandbox import task_sandbox`):
```python
from optimize import staging
```
Then, inside `run_evalset`'s per-task loop, call `stage_skills` right after the sandbox cwd is opened and before `run_task`. The current body is:
```python
        try:
            with task_sandbox(task) as cwd:
                t = {"id": task.id, "prompt": task.prompt, "cwd": cwd}
                run_result = run_task(t, config, runner=runner)
```
Change it to:
```python
        try:
            with task_sandbox(task) as cwd:
                staging.stage_skills(config, cwd)
                t = {"id": task.id, "prompt": task.prompt, "cwd": cwd}
                run_result = run_task(t, config, runner=runner)
```
(Leave everything else in the loop — the `to_jsonl`, `score_task`, the `except` isolation — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_evalrun.py -v`
Expected: PASS (all evalrun tests, incl. the new staging test)

- [ ] **Step 5: Full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 4 skipped)

- [ ] **Step 6: Commit**

```bash
git add optimize/evalrun.py tests/test_evalrun.py
git commit -m "feat(optimize): run_evalset stages a config's skills into the sandbox"
```

---

### Task 5: docs

**Files:**
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md`

- [ ] **Step 1: Update `README.md`** — note skills/MCP on a config

READ `README.md`. In the "Optimizer tier (preview)" section, after the "Optimizing (cost-first, budget-aware)" subsection (after its `--generate` paragraph), append:
```markdown
A config can also declare which **skills** it uses (a `skills:` name list in `metadata.yaml`,
selecting from your `~/.claude/skills` + plugins) and bring its own skills via a `skills/`
subdir (staged into each run's sandbox). MCP servers are set via `tools.json`'s `mcp_servers`.
These become optimization axes the generator can tune in a later step.
```

- [ ] **Step 2: Update `ROADMAP.md`**

READ `ROADMAP.md`. After the "Failure-driven generator (sub-project 4a) — done." paragraph under `## 1. Fix token accuracy (the blocker)`, add:
```markdown
**Skills/MCP wiring (sub-project 4b-i) — done.** A config's `skills` (name allow-list → the SDK
`skills` option, with `setting_sources`) and `skills_source` (dir staged into the run sandbox)
now actually apply; `available_skills`/`available_mcp` catalogs added. The generator's skills/MCP
mutation axes that consume these are sub-project 4b-ii.
```

- [ ] **Step 3: Update `CLAUDE.md`**

READ `CLAUDE.md`. Under `## Current state`, after the `optimize/` (generator) bullet, add:
```markdown
- `optimize/` (skills wiring) — `config.skills` (name list → SDK `skills` + `setting_sources`),
  `staging.stage_skills` (a config's `skills/` dir → the run sandbox's `.claude/skills`), and
  `catalog.available_skills`/`available_mcp`. Finishes the deferred skills→SDK wiring so a
  config's skills actually apply. Sub-project 4b-i; the generator's skills/MCP axes are 4b-ii.
```

- [ ] **Step 4: Run the full suite**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 4 skipped).

- [ ] **Step 5: Commit**

```bash
git add README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document skills/MCP wiring (4b-i)"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- A config with `skills: ["x"]` → `to_sdk_options()` has `skills==["x"]` and `setting_sources==["user","project"]`; a plain config's options are unchanged (no `skills`/`setting_sources`).
- `stage_skills` copies a config's `skills_source` into `cwd/.claude/skills` (no-op when absent), and `run_evalset` stages before the run (verified via the fake runner seeing the staged skill).
- `available_skills`/`available_mcp` enumerate fixtures and degrade to empty on missing/malformed input.
- `tokencast.py`/`tokencast.html`/`budget.py` unchanged; whole suite zero-spend, no SDK installed.
