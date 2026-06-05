# TokenCast Generator Skills/MCP Axes — Design Spec (sub-project 4b-ii of 7)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 4a (`generate_candidates`, the failure-driven generator), 4b-i
(`AgentConfig.skills`, `staging.stage_skills`, `catalog.available_skills`/`available_mcp`). All
merged to `main`.

---

## 1. Why this exists

The 4a generator can only mutate instructions and tool allow/deny lists. 4b-i finished the skills
wiring and built `available_skills`/`available_mcp` catalogs but left them unconsumed. This
sub-project **extends the failure-driven generator to the skills and MCP axes**: the LLM, shown
what each failing dimension is graded on and which skills/servers are available, proposes
candidates that turn the right skills/servers on. Pure extension of an existing seam — no new
modules.

---

## 2. Decisions already made (don't relitigate without reason)

- **Names only, validated against the catalogs.** The generator emits skill **names** and MCP
  server **names**. Each is validated against `skills_catalog` / `mcp_catalog`; unknowns are
  dropped; MCP names are resolved to their real configs **from the catalog**. The LLM never
  supplies a `command`/`args` — the catalog is the only source of server definitions. This is the
  security boundary: no arbitrary commands can be injected into a sandbox run.
- **Prompt enrichment = failing dims + their definitions + catalogs.** `generate_candidates`
  receives the `evalset`. For each failing `(task, dimension)`, the prompt surfaces that
  dimension's definition (name, `required` flag, judge rubric or rule summary), with **required
  (gating) dimensions listed first and tagged `[REQUIRED]`**, plus the available skill/MCP names.
- **Additive / union combine mode.** A candidate keeps the baseline's skills/MCP and adds the
  catalog-resolved ones the LLM picked. Adding capability, never silently stripping it.
- **Catalogs resolved in the CLI only** (the single filesystem touch) and injected down.
  `generate_candidates` stays pure: plain `list`/`dict` inputs, testable with no SDK and no
  filesystem.
- **Fully optional.** `--generate 0` (default) is byte-identical to 4a/3b; catalogs are only
  resolved when `--generate N > 0`.

---

## 3. Module layout (all modifications — no new files)

```
optimize/candidates.py  MODIFY: extend _build_candidate_prompt / _apply_mutation /
                                generate_candidates with evalset + skills_catalog + mcp_catalog;
                                add a dimension-describe helper + failing-dims-with-definitions helper
optimize/loop.py        MODIFY: run_optimize gains skills_catalog / mcp_catalog; forwards them
                                (with the evalset it already has) into generate_candidates
optimize/cli.py         MODIFY: --skills-dir / --mcp-catalog flags; resolve catalogs (only when
                                --generate > 0) and pass them through to run_optimize
```

`optimize/` may import `tokencast`; nothing here imports the SDK except the pre-existing
`# pragma: no cover` default generator in `candidates.py`.

---

## 4. Mutation surface + validation (`candidates.py`)

The generator's output object (one per candidate) gains two optional keys alongside the 4a keys:

```json
{ "system_prompt_append": "...", "allowed_tools": [...], "disallowed_tools": [...],
  "skills": ["pdf", "docx"], "mcp": ["playwright"], "note": "..." }
```

`_apply_mutation(baseline, mut, i, *, skills_catalog=None, mcp_catalog=None)` honors them:

- **skills:** `proposed = [s for s in mut.get("skills", []) if isinstance(s, str) and s in (skills_catalog or [])]`.
  If `proposed` is non-empty → `overrides["skills"] = _dedup((baseline.skills or []) + proposed)`
  (order-preserving). A baseline with `skills=None` is therefore promoted to the named list — this
  is intended and is also what enables discovery (4b-i emits `setting_sources` only when skills are
  set). If `proposed` is empty, skills is left as the baseline's (a fresh `list(...)` copy if it
  was a list, else `None`).
- **mcp:** `resolved = {name: mcp_catalog[name] for name in mut.get("mcp", []) if isinstance(name, str) and name in (mcp_catalog or {})}`.
  If `resolved` is non-empty → `overrides["mcp_servers"] = {**baseline.mcp_servers, **resolved}`
  (catalog config wins on a name collision — same server name, catalog is source of truth). If
  empty, `mcp_servers` is the baseline's (fresh `dict(...)` copy).
- **4a keys unchanged:** `system_prompt_append` (full replacement string), `allowed_tools`,
  `disallowed_tools`. Anything outside this surface (`model`, `budget_usd`, …) is still ignored.
- **No aliasing:** every mutable collection on the candidate is a fresh copy of either the
  override or the baseline's.

`generate_candidates(baseline, baseline_reports, *, n=2, generator=None, evalset=None,
skills_catalog=None, mcp_catalog=None)` builds the enriched prompt, calls the (injectable)
generator, and applies each mutation through `_apply_mutation` with the catalogs. Defaults
(`evalset=None`, empty catalogs) reproduce 4a behavior: no dimension definitions, no skills/MCP
axes (any names the LLM emits are dropped against the empty catalogs).

## 5. Prompt enrichment (`_build_candidate_prompt`)

Signature: `_build_candidate_prompt(baseline, baseline_reports, n, *, evalset=None,
skills_catalog=None, mcp_catalog=None)`.

- **Failing dimensions with definitions.** From `_failing_dimensions(baseline_reports)` (the
  existing `(task_id, dim_name, score)` tuples), cross-reference the `evalset` to find each
  `Dimension`. For each, emit a line with: the task id, dimension name, score, the `[REQUIRED]`
  tag when `dimension.required`, and a requirement description:
  - judge dim → the `judge` rubric string (truncated to a sane length, e.g. 300 chars)
  - rule dim → a one-line summary of its checks (e.g. `rule: file_exists path=pong.txt`,
    `command: pytest -q`)
  - **Required dimensions are sorted first.** If `evalset` is `None` (4a path), fall back to the
    plain `(task, dim, score)` lines with no definitions.
- **Catalogs.** Append:
  ```
  Available skills (choose by name): pdf, docx, web-search
  Available MCP servers (choose by name): playwright, filesystem
  ```
  Each line shows `(none available)` when its catalog is empty.
- **Output schema instructions.** Tell the model it may also include `"skills"` and `"mcp"` arrays
  of names chosen from those catalogs, in addition to the 4a keys.

## 6. Data flow & CLI (`loop.py`, `cli.py`)

- `run_optimize(..., evalset, ..., generator=None, n_generated=0, skills_catalog=None,
  mcp_catalog=None)`: when `n_generated > 0`, calls
  `candidates_mod.generate_candidates(baseline, baseline_reports, n=n_generated,
  generator=generator, evalset=evalset, skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)`.
  When `n_generated == 0`, behavior is unchanged (the new params are inert).
- `cmd_optimize`: two new flags on the `optimize` subparser:
  - `--skills-dir` (default `None` → `available_skills` falls back to `~/.claude/skills`)
  - `--mcp-catalog` (default `None` → `available_mcp` falls back to `~/.claude.json`'s `mcpServers`)
  Only when `args.generate > 0`, resolve `skills_catalog = catalog.available_skills(args.skills_dir)`
  and `mcp_catalog = catalog.available_mcp(args.mcp_catalog)`, and pass both into `run_optimize`.
  With no flags, the generator draws from the engineer's own installed skills/servers (the intended
  "optimize among what I have" behavior). The pre-flight estimate is unchanged (it already counts
  `--generate`).

## 7. Safety / isolation

- The catalog-only MCP resolution means the LLM cannot introduce a server config that runs an
  arbitrary command in the sandbox; it can only select servers the engineer already has.
- Unknown skill/MCP names are silently dropped (validated against the catalogs) — a hallucinated
  name can never reach `to_sdk_options`.
- Empty catalogs (no `~/.claude/skills`, no `~/.claude.json`) degrade to no skills/MCP axis: the
  prompt says none are available and any names are dropped. The instructions/tools axes still work.
- Staged skills (4b-i) land only in the throwaway sandbox cwd, never the user's repo.

## 8. Testing (zero spend, no SDK)

- `_apply_mutation`: additive union for skills (baseline-with-skills + proposed, deduped) and for
  mcp (baseline servers + catalog-resolved); unknown skill/MCP names dropped; baseline `skills=None`
  promoted to the proposed names; resolved MCP config pulled from the catalog (not the LLM); no
  aliasing of baseline collections.
- `_build_candidate_prompt`: a failing **required** dimension is tagged `[REQUIRED]` and listed
  before non-required ones; its judge rubric / rule summary appears; available skill + MCP names
  appear; empty catalog → `(none available)`; `evalset=None` → plain failure lines (4a fallback).
- `generate_candidates`: fake generator proposing `skills`/`mcp` → validated candidates with the
  additive result; empty catalogs → those axes dropped while instruction/tool mutations still apply.
- `run_optimize`: threads `skills_catalog`/`mcp_catalog` and the `evalset` into
  `generate_candidates` (verified via a fake generator that records what it received); `--generate 0`
  path unchanged.
- `cmd_optimize`: monkeypatched `run_optimize` receives `skills_catalog`/`mcp_catalog` resolved
  from `--skills-dir`/`--mcp-catalog`; catalogs are NOT resolved when `--generate 0`.
- One gated (`TOKENCAST_LIVE`) live smoke optional.
- Whole suite passes with no `claude-agent-sdk` installed.

## 9. Out of scope (later sub-projects)

- Task decomposition → sub-project 5.
- The `/tokencast-optimize` skill front door → sub-project 6.
- A hermetic `["project"]`-only `setting_sources` mode → future, if needed.
- Mutating MCP servers beyond the catalog (arbitrary configs) → explicitly rejected (safety).

## 10. Success criteria

- A fake generator returning `{"skills": ["pdf"], "mcp": ["playwright"]}` (with both in the
  catalogs) yields a candidate whose `skills` includes `pdf` (unioned with the baseline) and whose
  `mcp_servers` includes the catalog's `playwright` config.
- Unknown skill/MCP names in the generator output never reach the candidate.
- The generator prompt shows failing required dimensions first (tagged) with their requirement
  text, and the available skill/MCP names.
- `tokencast-optimize optimize … --generate N --skills-dir … --mcp-catalog …` works end to end;
  catalogs are resolved only when generating.
- `--generate 0` is byte-identical to 4a; whole suite zero-spend, no SDK installed.
- `tokencast.py`/`tokencast.html`/`budget.py` untouched.
