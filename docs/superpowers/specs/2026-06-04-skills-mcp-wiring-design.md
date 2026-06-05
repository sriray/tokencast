# TokenCast Skills/MCP Wiring — Design Spec (sub-project 4b-i of 7)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 1 (`AgentConfig`, harness), 2 (`run_evalset`/sandbox), 3b (loop). All
merged to `main`.

---

## 1. Why this exists

The skills axis was deferred since sub-project 1: `AgentConfig.skills_source` is read but never
applied at run time, and `to_sdk_options` doesn't touch skills. This sub-project **finishes that
wiring** so a config's skills actually take effect, and adds catalog helpers that sub-project
**4b-ii** (the generator's skills/MCP axes) will consume. Pure plumbing — no LLM, no `optimize`
CLI change.

### Verified SDK mechanism (the basis for this design)

The Agent SDK controls skills two ways, confirmed against current docs:
- **Discovery** via `setting_sources` (`["user","project","local"]`). If unset/`[]`, **no**
  filesystem skills load. `"project"` discovers `.claude/skills/` in `cwd` (and parents);
  `"user"` discovers `~/.claude/skills/`. Plugin skills load if the plugin is enabled.
- **Per-query allow-list** via the `skills` option: `["pdf","docx"]` (only those), `"all"`,
  or `[]` (none). A context filter over *discovered* skills.

So both mechanisms the user wants compose cleanly: **dir-staging** makes a config's own skills
discoverable (project), the **name allow-list** selects which discovered skills (staged + the
engineer's `~/.claude/skills` + plugins) the model may use.

---

## 2. Decisions already made (don't relitigate without reason)

- **Both mechanisms:** dir-staging (`skills_source` → sandbox `cwd/.claude/skills`) AND a name
  allow-list (`skills: list[str]` → the SDK `skills` option).
- **`setting_sources = ["user","project"]`** when skills are in play (so the name-filter can pick
  the engineer's installed skills *and* staged ones). Documented caveat: this also loads ambient
  user settings into eval runs (less hermetic) — the deliberate cost of optimizing among installed
  skills. Plain configs (no skills) set no `setting_sources` and are unchanged.
- **No CLI / no LLM in 4b-i.** Catalog helpers are built here but consumed in 4b-ii.
- **Plumbing is testable without the SDK** (to_sdk_options is a plain dict; staging is filesystem;
  catalogs are filesystem/JSON). The actual skill-loading effect is validated by 4b-ii's live smoke.

---

## 3. Module layout

```
optimize/config.py    MODIFY: add `skills: Optional[list[str]]`; load/save; map in to_sdk_options
optimize/staging.py   NEW: stage_skills(config, cwd)
optimize/catalog.py   NEW: available_skills(skills_dir) + available_mcp(catalog_path)
optimize/evalrun.py   MODIFY: call stage_skills(config, cwd) before harness.run (in the sandbox)
```

`optimize/` may import `tokencast`; nothing here imports the SDK. `staging.py`/`catalog.py` are
stdlib-only (`os`, `shutil`, `json`).

---

## 4. `AgentConfig.skills` + `to_sdk_options` (`config.py`)

- New field: `skills: Optional[List[str]] = None` (skill **names**; `None` = leave the SDK option
  unset = SDK default; `[]` = no skills; `["a","b"]` = only those).
- `load`: `skills = meta.get("skills")`; if present it must be a list of strings (else a clean,
  path-prefixed `ValueError`, consistent with the existing validation). `skills_source` detection
  (the `skills/` subdir) is unchanged.
- `save`: write `skills` into `metadata.yaml` when not `None`.
- `to_sdk_options`:
  - `if self.skills is not None: opts["skills"] = list(self.skills)`.
  - `if self.skills is not None or self.skills_source: opts["setting_sources"] = ["user", "project"]`.
  - Everything else unchanged. A config with neither `skills` nor `skills_source` produces the
    exact same dict as today.

> Note: the existing `test_to_sdk_options_maps_fields` fixture creates a `skills/` subdir, so its
> config now has `skills_source` set → `to_sdk_options` now emits `setting_sources`. That test's
> "`setting_sources` not in opts" assertion is updated to expect `["user","project"]` — this is the
> deferred wiring being finished, not a regression.

---

## 5. Dir staging (`optimize/staging.py`)

```
stage_skills(config, cwd) -> None
```
- If `config.skills_source` is falsy or not a directory → no-op.
- Else copy each entry of `skills_source` into `cwd/.claude/skills/` (subdirs via
  `shutil.copytree(..., dirs_exist_ok=True)`, files via `copy2`); `makedirs` the dest first.
- Pure filesystem; never invokes the SDK. Idempotent (re-staging overwrites).

`run_evalset` (sub-project 2) calls `staging.stage_skills(config, cwd)` immediately after opening
the sandbox `cwd` and before `harness.run`, so staged skills are present in the run's working dir.
(This is the one evalrun change; the rest of the eval flow is untouched.)

---

## 6. Catalogs (`optimize/catalog.py`) — built here, consumed in 4b-ii

- `available_skills(skills_dir=None) -> list[str]`: defaults to `~/.claude/skills`; returns the
  sorted names of subdirectories that contain a `SKILL.md`. Missing dir → `[]`.
- `available_mcp(catalog_path=None) -> dict`: returns `{server_name: server_config}`. With an
  explicit `catalog_path`, reads that JSON (accepting either a bare `{name: cfg}` map or a
  `{"mcpServers": {...}}` wrapper). With no path, falls back to `~/.claude.json`'s `mcpServers`.
  Missing/malformed → `{}` (never raises).

These produce data only; they have no effect until 4b-ii's generator feeds them into its prompt.

---

## 7. Safety / isolation

- Staged skills are copied into the **sandbox** `cwd` (a throwaway temp dir / worktree from
  sub-project 2), never the user's repo.
- `setting_sources=["user","project"]` loads ambient user skills *and* user settings into eval
  runs — a deliberate, documented trade-off (the skills axis optimizes among installed skills).
  A future flag could force `["project"]`-only for hermetic runs; out of scope for 4b-i.
- Catalog readers never raise on missing/malformed input (degrade to empty), so a missing
  `~/.claude.json` or `~/.claude/skills` is harmless.

---

## 8. Testing (zero spend, no SDK)

- `to_sdk_options`: `skills=["a"]` → `opts["skills"]==["a"]` and `opts["setting_sources"]==["user","project"]`;
  `skills=None` + no `skills_source` → neither key present (plain config dict unchanged);
  a `skills_source`-only config → `setting_sources` present, no `skills` key. **Update** the
  existing fixture-with-`skills/`-dir test accordingly.
- `config.load/save`: `skills` list round-trips through `metadata.yaml`; a non-list `skills` →
  clean `ValueError`.
- `stage_skills`: copies a fixture skills dir (incl. a nested `SKILL.md`) into `cwd/.claude/skills`;
  no-op when `skills_source` is None or not a dir.
- `available_skills`: enumerates fixture skill dirs (only those with `SKILL.md`); missing dir → `[]`.
- `available_mcp`: reads a bare-map fixture and a `{"mcpServers":...}` fixture; missing/malformed → `{}`.
- `evalrun`: a config with `skills_source` set → after the run, the sandbox `cwd/.claude/skills`
  contains the staged skill (verified via a fake runner that records/inspects its `cwd`).
- Whole suite passes with no `claude-agent-sdk` installed.

---

## 9. Out of scope (4b-ii and later)

- The generator's skills/MCP mutation axes (using these catalogs) + the required-dimension prompt
  enrichment → sub-project 4b-ii.
- A `--skills-dir`/`--mcp-catalog` CLI surface for the generator → 4b-ii.
- A hermetic `["project"]`-only setting_sources mode → future, if needed.
- Task decomposition → 5; skill front door → 6.

## 10. Success criteria

- A config with `skills: ["x"]` in `metadata.yaml` produces `to_sdk_options()` with
  `skills==["x"]` and `setting_sources==["user","project"]`.
- A config dir containing a `skills/` subdir gets that dir staged into the run's sandbox
  `cwd/.claude/skills` by `run_evalset`.
- `available_skills`/`available_mcp` enumerate real entries from fixtures and degrade to empty on
  missing/malformed input.
- Configs with no skills are byte-for-byte unchanged in `to_sdk_options` (optionality).
- The whole suite passes zero-spend with no SDK installed.
