# TokenCast Harness — Design Spec (sub-project 1 of 5)

**Date:** 2026-06-03
**Status:** Approved design, pending implementation plan
**Scope:** The execution + telemetry substrate for turning TokenCast from a cost
*forecaster* into a closed-loop *optimizer* for a locally-running Claude (Claude Code),
analogous to Microsoft Foundry's Agent Optimizer — but cost/time-first.

---

## 1. Why this exists

TokenCast today forecasts what an agentic coding task will cost and how long it takes,
calibrated on Claude Code's JSONL transcripts. Two problems cap its usefulness:

1. **The trust blocker.** Claude Code's JSONL `input_tokens` is a streaming placeholder
   (0 or 1 in ~75% of entries), so absolute costs are a *floor*. This is documented loudly
   in the tool and is roadmap item #1.
2. **It only looks, never acts.** It estimates cost; it can't *reduce* it.

The user's goal: extend TokenCast to do everything Agent Optimizer does **plus more** —
optimize across model choice, instructions, skills, MCP tools, and even task decomposition
(routing subtasks to the cheapest capable model) — while keeping the existing `forecast`
feature simple and untouched.

That full vision is a **platform of 5 sub-projects**, not one change. This spec covers only
**sub-project 1: The Harness** — the foundation every other piece depends on.

### The decomposition (build order, agreed)

```
0/1. THE HARNESS   (this spec)  accurate execution + telemetry via the Agent SDK
2.   EVAL HARNESS               task sets + pass/fail criteria + composite 0-1 scorer
3.   OPTIMIZE LOOP              baseline -> candidates -> re-eval -> rank (Pareto) -> promote
4.   OPTIMIZATION AXES          candidate generators: model / instructions / skills / MCP
5.   TASK DECOMPOSITION         split a task, route subtasks to cheapest capable model
6.   SKILL FRONT DOOR           /tokencast-optimize: conversational entry point + the LLM
                                judgment half (candidate generation, result interpretation,
                                recommendation). Built once there is an engine worth wrapping.
```

Each layer is useless without the one below it: you cannot rank candidates by cost if cost
is the broken floor (→ harness first), and you cannot tell "better" from "cheaper but dumber"
without a scorer (→ eval harness before any optimization).

### The 3-layer architecture (skill vs. code boundary)

The split between deterministic code and LLM judgment is deliberate. TokenCast's value is
*trustworthy numbers*, so anything that measures money must be reproducible code, not an LLM
improvising the math each run. Conversely, generating candidate configs and interpreting
results is judgment — skill-shaped.

```
Layer 3  SKILL(s)   /tokencast-optimize -> conversational front door + LLM candidate
                    generation + result interpretation                   (judgment; sub-project 6)
            | calls
Layer 2  ENGINE     optimize/ package (Agent SDK): harness, eval scorer,
                    optimize loop, pricing                                (deterministic; sub-projects 1-5)
            | writes JSONL
Layer 1  LIGHT CORE tokencast.py — forecast/report/demo, stdlib-only      (unchanged)
```

This sits on top of the two-tier dependency boundary (§2): Layer 1 = light tier; Layers 2-3 =
heavy tier. The skill (Layer 3) is a thin wrapper over the deterministic engine (Layer 2) —
it never reimplements measurement or cost math, only orchestrates and reasons. Self-referential
note: because TokenCast optimizes Claude Code configs (including which skills load), shipping
its front door as a skill means it lives inside the environment it tunes.

---

## 2. Keystone decisions (already made)

- **Wrapper mechanism = Claude Agent SDK harness** (not an API proxy, not OTel-only).
  Rationale: the user is assumed to be running Claude Code locally. The optimizer needs a
  *controlled, repeatable* execution path it can configure per run — exactly what the SDK
  gives. Verified SDK capabilities:
  - Final result message exposes **real aggregated token counts** (`usage` with
    input/output/cache split) and a **per-model breakdown** (`model_usage`), plus
    `num_turns` and `duration_ms`. The cumulative `usage` is *not* the streaming
    placeholder — **this fixes the trust blocker** on any run TokenCast drives.
  - Per-run configurable: `model`, `system_prompt` (preset + append), `allowed_tools` /
    `disallowed_tools`, `mcp_servers`.
  - Guardrails: `max_budget_usd`, `max_turns`.
  - Caveats baked into this design:
    - SDK's own `total_cost_usd` is a *client-side estimate* (bundled price table). We
      therefore keep **our** live-refreshable pricing and apply it to the SDK's accurate
      *token counts*. (Tokens accurate; dollars we compute, as today. Optional later:
      reconcile against the Usage & Cost API for dollar ground-truth.)
    - **Skills are not per-query toggleable** — they load from the filesystem
      (`.claude/skills`). Optimizing "which skills" (axis in sub-project 4) requires
      swapping a staged config dir via `setting_sources`, not a flag. The harness supports
      pointing at a staged skills dir; the optimization *over* it is later work.
    - Subagent models are capped to `{sonnet, opus, haiku}` aliases, one level deep — fine
      for task-decomposition routing (those are the three tiers). Deferred to sub-project 5.
  - **Auth and billing — TokenCast is deliberately agnostic to it.** The SDK needs *an*
    auth source to run, in one of two modes:
    - `ANTHROPIC_API_KEY` → metered pay-as-you-go **dollars**; SDK result carries a real
      `total_cost_usd`.
    - The user's **Claude plan (Pro/Max OAuth)** → consumes plan credits / rate limits;
      there is **no per-run dollar figure**, only token counts + credit consumption.

    The key invariant: **accurate token counts are available in either mode.** TokenCast
    already derives dollars by applying its own (live-refreshable) pricing to token counts,
    so it reports cost as a *modeled* dollar figure ("this run = $X at list prices")
    regardless of how the run was actually billed — which is exactly what a
    forecasting/estimation tool should report. Therefore the harness requires *an* auth
    source but is **not** locked to metered API billing.

    (Note: the precise plan-billing details — credit-pool sizes, policy dates — could not
    be reliably confirmed and are not load-bearing here; the token-count invariant is what
    the design depends on. There is an official "Use the Agent SDK with your Claude plan"
    path, so plan-based runs are supported.)

    Real spend still happens (dollars *or* plan credits), so the harness forecasts run cost
    and confirms before spending above a threshold (dogfood tie-in, §6).

- **Dependency boundary = two-tier package.** The existing `forecast`/`report`/`demo` stay
  stdlib-only, offline-capable, and dependency-free. The harness/optimizer live in a separate
  `optimize/` tier that imports the Agent SDK, installed only via `pip install tokencast[optimize]`.
  - Boundary rule: `tokencast.py` imports **nothing** from `optimize/`. `optimize/` *may*
    import `tokencast` (to reuse pricing). **The two tiers connect through a file format
    (JSONL), not a code dependency.**

---

## 3. Package structure

```
tokencast/
  tokencast.py          # UNCHANGED — stdlib-only forecast/report/demo (the simple feature)
  tokencast.html        # UNCHANGED
  pyproject.toml        # NEW — `pip install tokencast` = light core;
                        #       `pip install tokencast[optimize]` pulls claude-agent-sdk
  optimize/             # NEW heavy tier (imports the Agent SDK)
    __init__.py
    config.py           # AgentConfig — the mutable unit the optimizer will later mutate
    harness.py          # run(task, config) -> RunResult   (drives the SDK, captures the stream)
    result.py           # RunResult + writes JSONL in Claude Code's schema (accurate tokens)
    pricing.py          # imports tokencast pricing; applies it to accurate token counts
    cli.py              # `tokencast-optimize run <taskfile> --config <dir>`
  docs/superpowers/specs/...
```

---

## 4. The Config (`optimize/config.py`)

The unit the optimizer (sub-projects 3–4) will later mutate. Mirrors Agent Optimizer's
`.agent_configs/` directory layout so it is familiar, and maps 1:1 onto SDK options.

```
configs/baseline/
  metadata.yaml      # model alias, budget_usd cap, max_turns
  instructions.md    # -> system_prompt append            (instruction axis, 3b)
  tools.json         # -> allowed_tools / disallowed_tools / mcp_servers   (tool axis, 3d)
  skills/            # -> staged .claude/skills via setting_sources         (skills axis, 3c)
```

- `AgentConfig.load(dir) -> AgentConfig` — reads the directory into a dataclass.
- `AgentConfig.to_sdk_options() -> ClaudeAgentOptions` — produces the SDK options object.
- Fields: `model: str`, `system_prompt_append: str`, `allowed_tools: list[str]`,
  `disallowed_tools: list[str]`, `mcp_servers: dict`, `skills_source: Path | None`,
  `budget_usd: float | None`, `max_turns: int | None`, `config_id: str`.
- Missing files degrade gracefully to SDK defaults (e.g. no `instructions.md` → no append).

Task decomposition (axis 3e) is **not** modeled here — it is multi-config orchestration that
builds on this single-config unit, handled in sub-project 5.

---

## 5. The Harness and RunResult

### `optimize/harness.py`

`run(task: Task, config: AgentConfig, *, runner=None) -> RunResult`

1. Build `ClaudeAgentOptions` from `config.to_sdk_options()`.
2. Run `query()` (async), iterate the message stream, capture the full transcript and the
   final `ResultMessage`.
3. Enforce guardrails: `max_budget_usd` and `max_turns` from config metadata.
4. Read **accurate token counts** from `usage` / `model_usage`; compute dollars via
   `optimize/pricing.py` (which reuses `tokencast.PRICING`, live-refreshable).
5. Return a `RunResult`.

`runner` is an injectable seam: defaults to the real SDK `query`; tests inject a fake that
replays recorded fixtures (no spend).

`Task` (minimal for this sub-project): `{id: str, prompt: str, cwd: Path | None}`.
Pass/fail **criteria are deliberately NOT here** — scoring is sub-project 2. This keeps the
harness a pure execution + measurement unit.

### `optimize/result.py`

`RunResult` dataclass:
`task_id, config_id, model_usage{per-model: input/output/cache_read/cache_write},
cost_usd, duration_ms, num_turns, transcript[], final_output, files_changed[], accurate=True`.

- `RunResult.to_jsonl(path)` — emits lines in **Claude Code's JSONL schema** (with accurate
  token counts in the usage fields) so the untouched `tokencast.py` can `report`/`forecast`
  on them directly. This is the bridge between the two tiers.
- `accurate=True` marks that token counts came from an SDK result (vs. a scraped floor),
  so downstream consumers can distinguish calibration-grade data.

---

## 6. CLI and the dogfood tie-in

`tokencast-optimize run taskfile.md --config configs/baseline/ --budget 2.00 --out logs/`

- **Pre-flight forecast:** before a run incurs real spend (metered **dollars** *or* Claude
  **plan credits** — see §2), call the existing `forecast` logic to predict the run's cost
  from history; if the p90 estimate exceeds a threshold (or `--budget`), print
  `est. p90 $X — proceed? [y/N]` and require confirmation. The dollar figure is the modeled
  list-price cost derived from forecasted tokens, shown regardless of billing mode.
  *TokenCast forecasts its own spend before spending it.*
- **Output:** writes `RunResult.to_jsonl()` into `--out`, immediately consumable by
  `python tokencast.py report logs/` and `forecast logs/`.
- `--budget` maps to the SDK `max_budget_usd` guardrail as a hard ceiling.

---

## 7. Testing

- **Injectable runner.** `harness.run(..., runner=fake)` replays recorded SDK message
  streams + `ResultMessage`s from fixtures — full coverage with zero spend.
- **Unit tests:** `AgentConfig.load` (incl. missing files), `to_sdk_options()` mapping,
  `RunResult` parsing from canned results, `to_jsonl()` round-trips through `tokencast.py`'s
  parser (the bridge contract).
- **One opt-in live smoke test:** gated behind an env var (e.g. `TOKENCAST_LIVE=1`) and a
  tiny `--budget`, runs a trivial task end-to-end against the real SDK.

---

## 8. Explicitly out of scope (later sub-projects)

- Pass/fail criteria and composite scoring → sub-project 2.
- Candidate generation, ranking, Pareto selection, promote/deploy → sub-project 3.
- Model/instruction/skills/MCP optimization *strategies* → sub-project 4.
- Task decomposition and multi-model subtask routing → sub-project 5.
- Usage & Cost API reconciliation for dollar ground-truth → optional later enhancement.

## 9. Success criteria for sub-project 1

- `tokencast-optimize run` executes a real task headless and produces a `RunResult` with
  **accurate** token counts (not the streaming-placeholder floor).
- The emitted JSONL is read by the unchanged `tokencast.py report`/`forecast` without error.
- The light tier (`tokencast.py`, `tokencast.html`) remains stdlib-only and SDK-free.
- The pre-flight forecast confirmation fires before any real spend above threshold.
- Tests pass with zero API spend via the injectable runner.
