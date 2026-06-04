# TokenCast Failure-Driven Candidate Generator — Design Spec (sub-project 4a of 7)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 1 (harness/`AgentConfig`), 2 (eval harness: `run_evalset`,
`EvalReport`, `TaskScore`), 3b (the optimize loop: `run_optimize`, `ranking`, `candidates`).
All merged to `main`.

---

## 1. Why this exists

Sub-project 3b ranks candidate configs cost-first, but the candidates come from dumb sources
(model sweep, hand-supplied dirs). This sub-project adds the **smart** half: an LLM generator
that reads the **baseline's eval failures** and proposes targeted config changes to fix them —
Microsoft Agent Optimizer's "generate candidates based on identified failures."

This is **sub-project 4a**: the generator + the integrated generate→eval→rank loop, covering the
**instructions** and **tool-selection** axes (the two that map cleanly to `to_sdk_options` today).
**Sub-project 4b** (separate) finishes the deferred skills→SDK wiring and adds the skills & MCP
axes to the same generator's mutation surface.

### One generator, not four

The axes are not separate generators. There is **one** failure-driven generator: given the
baseline config + its failing dimensions + a *mutation surface* (the fields it may change), it
returns candidate `AgentConfig`s. In 4a the mutation surface is `{system_prompt_append,
allowed_tools, disallowed_tools}`; 4b widens it. This keeps it a single, injectable LLM seam.

---

## 2. Decisions already made (don't relitigate without reason)

- **Failure-driven, integrated:** eval the baseline first, feed its failing tasks/dimensions to
  the generator, then eval+rank the proposed candidates. Not a standalone step.
- **Mutation surface (4a) = instructions + tool-selection** (`system_prompt_append`,
  `allowed_tools`, `disallowed_tools`). Skills/MCP are 4b.
- **One injectable seam:** `generate_candidates(..., generator=None)`; default uses the Agent SDK
  lazily; tests inject a fake → zero-spend suite, no SDK installed. (Same pattern as `judge`,
  `generate_evalset`.)
- **The generator sees the full baseline `EvalReport`s** (per-task `TaskScore.dimension_scores`),
  not the aggregate `CandidateResult` — it needs per-dimension failure detail.
- **Fully optional:** with no generator (`--generate 0`, the default), the loop behaves exactly
  as 3b. Lives in `candidates.py` next to the deterministic sources.

---

## 3. Module layout

```
optimize/candidates.py   MODIFY: add generate_candidates() + the default SDK generator + helpers
optimize/loop.py         MODIFY: run_optimize gains generator=, n_generated=; baseline-first ordering
optimize/cli.py          MODIFY: optimize subcommand gains --generate N; pre-flight counts it
```

`candidates.py` already holds `model_sweep`/`from_dirs`. The default SDK generator lazily imports
`claude_agent_sdk` inside its function, so importing `candidates` stays SDK-free. `ranking.py` is
untouched.

---

## 4. The generator (`optimize/candidates.py`)

`generate_candidates(baseline, baseline_reports, *, n=2, generator=None) -> list[AgentConfig]`:
- `baseline`: the baseline `AgentConfig`.
- `baseline_reports`: `list[EvalReport]` from evaluating the baseline (≥1; more with repeats).
- `generator`: `(prompt: str) -> list[dict]`. Defaults to `_default_candidate_generator`.

Flow:
1. `prompt = _build_candidate_prompt(baseline, baseline_reports, n)`.
2. `mutations = generator(prompt)` — a list of mutation dicts (at most `n` honored).
3. For each mutation `i`: `_apply_mutation(baseline, mut, i) -> AgentConfig`, appended.

`_build_candidate_prompt(baseline, baseline_reports, n)`:
- Includes the baseline's current `system_prompt_append` and `allowed_tools`/`disallowed_tools`.
- Summarizes **failing dimensions** drawn from `report.tasks[].dimension_scores` (and `passed`):
  e.g. "task `slugify`: dimension `tests_pass` (required) scored 0.0; `clarity` scored 0.4".
  Aggregates across `baseline_reports` (lists each failing (task, dimension, score)).
- Instructs: return ONLY JSON, a list of ≤ n mutation objects, each with an optional
  `system_prompt_append` (full replacement string) and/or `allowed_tools`/`disallowed_tools`
  (lists), plus a short `note`. No other fields.

`_apply_mutation(baseline, mut, i) -> AgentConfig`:
- `dataclasses.replace(baseline, config_id=f"{baseline.config_id}-gen{i+1}")` then override only
  the **mutation-surface** fields present in `mut`: `system_prompt_append` (str), `allowed_tools`
  / `disallowed_tools` (coerced to `list`). Fields outside the surface are **ignored**.
- Mutable collections are passed as fresh copies (no aliasing the baseline), consistent with
  `model_sweep`.
- If a mutation provides none of the surface fields, it still yields a (degenerate) copy with a
  new id; the loop's zero-cost/ranking logic handles a no-op candidate harmlessly.

`_default_candidate_generator(prompt)` / `_generate_sdk(prompt)`: `# pragma: no cover`; lazily
`from claude_agent_sdk import query, ClaudeAgentOptions` (model `sonnet`); parse the first JSON
array from the reply; return `[]` on no parse (loop then just has no generated candidates).

---

## 5. Loop integration (`optimize/loop.py`)

`run_optimize(baseline, candidates, evalset, *, runner=None, judge=None, repeats=1,
min_quality=None, by="cost", out_dir="runs", promote_to=None, budget_remaining=None,
need_tasks=None, generator=None, n_generated=0)`:

Reordered so generation is failure-driven:
1. Eval the **baseline** ×repeats via `run_evalset` → `baseline_reports`; `ranking.aggregate` →
   baseline `CandidateResult`.
2. If `generator` and `n_generated > 0`:
   `generated = candidates_mod.generate_candidates(baseline, baseline_reports, n=n_generated,
   generator=generator)`; append to the working candidate list (deduped by `config_id`).
3. Eval each remaining config (supplied + model-sweep + generated) ×repeats → aggregate. (The
   baseline is not re-evaluated; its result from step 1 is reused.)
4. `ranking.build_result(...)` (floor, Pareto, budget pass) → write `optimize.json` → promote.

With `generator=None`/`n_generated=0`, steps 1+3 evaluate exactly the same set as 3b and produce
an identical result (baseline-first ordering doesn't change ranking).

---

## 6. CLI (`optimize/cli.py`)

`tokencast-optimize optimize EVALSET --config BASELINE … [--generate N]`:
- `--generate N` (default 0): generate N failure-driven candidates via the default SDK generator.
- Pre-flight estimate now sums **all** configs: `(1 + n_supplied + n_sweep + N) × n_tasks ×
  repeats`, printed to stderr, confirm-before-spend unless `--yes`.
- `cmd_optimize` passes `generator=` (the default SDK generator when `--generate>0`, else None)
  and `n_generated=args.generate` to `run_optimize`. Generated candidates appear in the ranked
  table like any other (winner/Pareto/runway columns all apply).

---

## 7. Safety

- Generated candidates run only through the same isolated `run_evalset` sandbox path — no new
  execution surface. The generator only edits `system_prompt_append`/tool lists; it cannot inject
  shell or files. (Rule checks in the eval set are still author-controlled per sub-project 2.)
- Pre-flight makes the larger candidate set's spend visible and confirmed.
- The generator's output is validated through `_apply_mutation`'s allow-list of fields — anything
  outside the 4a mutation surface is dropped, so a wild LLM reply can't change unexpected config.

---

## 8. Testing (zero spend, no SDK)

- `generate_candidates` with a fake generator returning canned mutation dicts: candidates carry
  the mutated `system_prompt_append`/tools, fresh collections, distinct `config_id`s; a mutation
  with an out-of-surface field (e.g. `model`) has that field ignored; an empty mutation list →
  `[]`.
- `_build_candidate_prompt`: asserts the prompt contains the baseline instructions and at least
  one failing (task, dimension) drawn from a synthetic `EvalReport` with a failed `TaskScore`.
- `run_optimize` with a fake generator + fake runner (cost varies by model) + fake judge:
  baseline evaluated first, a generated candidate evaluated and eligible to win; `optimize.json`
  + `promoted/` written. **Optionality:** `generator=None` yields the same winner as the
  no-generator path on the same inputs.
- CLI: `--generate 2` with a monkeypatched `run_optimize` asserts `generator` is non-None and
  `n_generated==2` are passed through; pre-flight count includes the generated configs.
- Default SDK generator `# pragma: no cover`; one opt-in `TOKENCAST_LIVE` smoke (baseline +
  `--generate 1` on a one-task set, tiny budget).

---

## 9. Out of scope (later)

- Skills→SDK wiring + skills & MCP axes in the mutation surface → sub-project 4b.
- Task decomposition / multi-model routing → sub-project 5.
- The `/tokencast-optimize` skill front door → sub-project 6.
- Iterative/multi-round generation (re-generate from the winner's residual failures) → future;
  4a does a single generation round off the baseline.

## 10. Success criteria

- `tokencast-optimize optimize evalset.yaml --config baseline/ --generate 2` evaluates the
  baseline, generates 2 failure-driven candidates (instructions/tools mutations), evaluates and
  ranks them with the rest, and promotes the winner — with the generated candidates visible in
  the ranked table.
- The generator only ever changes `system_prompt_append`/`allowed_tools`/`disallowed_tools`;
  out-of-surface fields in its output are ignored.
- With `--generate 0` (default), the loop output is identical to sub-project 3b (optionality).
- The whole suite passes zero-spend with no `claude-agent-sdk` installed.
