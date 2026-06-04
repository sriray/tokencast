# TokenCast Eval Harness — Design Spec (sub-project 2 of 6)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Depends on:** sub-project 1 (The Harness) — `harness.run`, `RunResult` (+`to_jsonl`),
`AgentConfig`, `pricing`. All merged to `main`.

---

## 1. Why this exists

The harness (sub-project 1) can run one task under one config and measure it accurately.
But you cannot *optimize* what you cannot *score*. The eval harness adds the scoring layer:
given a task and a way of running it, produce a **quality score** plus accurate cost/time, so
the optimize loop (sub-project 3) can hold quality constant while driving cost/time down.

This is the precondition for every optimization axis. It mirrors Microsoft Agent Optimizer's
evaluate step (composite 0–1 score, per-task pass/fail, token cost per run) but is **hybrid**
(deterministic rule checks + LLM judge) and **cost/time-first**.

### The two scenarios this must serve (and how)

Both reduce to the same shape — `(task prompt + exit criteria + how-to-run) → score` — which
is exactly what this harness produces:

- **Greenfield project:** user defines a project + exit criteria + plan. Task runs in an empty
  fresh temp dir.
- **Existing codebase:** user defines a task + exit criteria + plan against their repo. Task
  runs seeded from the repo (copy or git worktree).

Their terms map onto the model: *task/plan* → EvalTask `prompt`; *eval/exit criteria* →
the task's `dimensions` (rule gates + judge dimensions).

### Two optimization *targets* (decided here, exercised in sub-project 3)

The eval harness is target-agnostic — it just scores `(task, approach)`. Sub-project 3 chooses
which knob to vary:

- **Target A — optimize a reusable *config*:** tasks fixed (an eval set), vary
  model/instructions/skills/MCP → a config you keep reusing (Agent Optimizer's model).
- **Target B — optimize *how to run this specific task*:** task fixed (the user's work item),
  vary the approach (model, decomposition) → the cheapest/fastest way to hit *this* task's exit
  criteria.

**Requirement on this sub-project:** a **single-task eval set is first-class** (so Target B
works), and tasks can be **seeded from an existing repo** (so existing-codebase work works).

### The economic caveat (stated honestly; shapes sub-project 3, not this one)

Optimizing *how to run a single one-off task* by trial means running it N times to find the
best of N — you pay N× before you get the deliverable. That pays off only when the work is
**recurring** (amortized over future runs), the task is **large** enough that the cheaper
approach saves more than the trials cost, or you optimize on a **small representative slice**
then apply the winner. For a genuine one-off, the right tool is the **forecast** (cheap,
up-front, no trials). Sub-project 3 will steer accordingly; the eval harness simply makes
honest scoring available either way.

---

## 2. Decisions already made (don't relitigate without reason)

- **Scoring = hybrid.** Deterministic rule checks (the trustworthy backbone) + an LLM judge for
  qualitative dimensions. Composite 0–1 from weighted dimensions.
- **Cold-start generation is included** (`eval init`): an LLM drafts an eval set from the
  repo/CLAUDE.md, behind an injectable seam, written as a human-review draft.
- **Isolation = fresh temp dir, two seed modes:** copy (greenfield/templates) and git worktree
  (existing repos — cheap, shares the object store, auto-cleanup).
- **Injectable seams for anything that spends money** (the runner from sub-project 1, the judge,
  the generator), so the suite runs with zero API spend and no SDK installed.
- **Cost is always computed from accurate `RunResult` tokens via `pricing`** — never the SDK's
  own `total_cost_usd`.

---

## 3. Module layout (all new under `optimize/`, plus a CLI extension)

```
optimize/evalset.py    EvalTask / Dimension / EvalSet data model + YAML load/save
optimize/checks.py     deterministic checks (command / file_exists / file_contains) -> bool
optimize/judge.py      LLM judge behind an injectable seam -> per-dimension 0-1
optimize/scorer.py     combine checks + judge into a TaskScore (per-dim, composite, passed)
optimize/sandbox.py    per-task isolation: fresh temp dir, seed via copy or git worktree
optimize/evalrun.py    orchestrate: per task -> sandbox -> harness.run -> score -> EvalReport
optimize/generate.py   cold-start `eval init`: LLM drafts an EvalSet (injectable seam)
optimize/cli.py        EXTEND with `eval run` and `eval init` subcommands
```

Each file has one responsibility. `optimize/` may import `tokencast` (pricing reuse); the light
tier never imports `optimize/`. Sandbox is split out from evalrun because isolation (temp
dirs/worktrees, cleanup) is a distinct concern worth testing independently.

---

## 4. The eval-set data model (`optimize/evalset.py`)

A **unified dimension** model: every dimension has a weight and is scored *either* by rules
*or* by the judge. (Chosen over separate checks/judge lists — cleaner and uniform.)

```yaml
# evals/<name>/evalset.yaml
tasks:
  - id: slugify
    prompt: "Add a slugify(text) function to slug.py with assert tests, and run them."
    # Isolation: at most one of seed_dir / seed_repo. Absent => empty temp dir (greenfield).
    seed_dir: seed/                 # copy this dir into the task's fresh temp dir
    # seed_repo: { path: "../myrepo", ref: "main" }   # OR: git worktree off this repo@ref
    pass_threshold: 0.6             # composite >= this AND all required dims == 1.0 => passed
    dimensions:
      - name: tests_pass
        weight: 3
        required: true              # required dim scoring < 1.0 => task fails regardless of composite
        rule: { kind: command, cmd: "python slug.py", expect_exit: 0 }
      - name: file_present
        weight: 1
        rule: { kind: file_exists, path: slug.py }
      - name: code_clarity
        weight: 2
        judge: "Is the function readable, with clear names and no dead code? Rate 0-1."
```

Dataclasses:
- `Check` — `kind` ∈ {`command`, `file_exists`, `file_contains`}, plus kind-specific fields
  (`cmd`/`expect_exit`, `path`, `path`+`pattern`).
- `Dimension` — `name: str`, `weight: float`, `required: bool=False`, and exactly one of
  `rule: Check` (or a list of Checks) **or** `judge: str` (rubric). Validation rejects a
  dimension with both or neither. `required` is intended for **rule** dimensions (the "tests
  must pass" gate): a required dimension fails the task if its score < 1.0. Marking a judge
  dimension `required` is allowed but discouraged (judges rarely score exactly 1.0); validation
  emits a warning, not an error.
- `EvalTask` — `id`, `prompt`, `pass_threshold: float=0.6`, `dimensions: list[Dimension]`,
  and optional isolation: `seed_dir: str | None`, `seed_repo: {path, ref} | None` (mutually
  exclusive; validation rejects both).
- `EvalSet` — `tasks: list[EvalTask]` (length 1 is first-class). `EvalSet.load(path)` /
  `save(path)` round-trip YAML, with the same loud-failure validation discipline as
  `AgentConfig` (named errors with file context on malformed input).

A **rule dimension's score** = fraction of its checks passing (one or several checks per dim).
A **judge dimension's score** = the judge's 0–1.

---

## 5. Deterministic checks (`optimize/checks.py`)

`run_check(check: Check, cwd: str) -> bool`, run inside the task's sandbox dir:
- `command` — run `cmd` via subprocess in `cwd` (with a timeout); pass iff exit code ==
  `expect_exit` (default 0).
- `file_exists` — `os.path.exists(cwd/path)`.
- `file_contains` — file exists and its text matches `pattern` (regex).

Pure code, no LLM. Unit-tested directly with real temp dirs + trivial commands (cheap, no spend).
Commands run a shell; this is the same trust model as a test config — see Safety (§10).

---

## 6. The judge (`optimize/judge.py`)

`score_dimension(run_result: RunResult, dimension: Dimension, judge=None) -> float`:
- The default `judge` makes one LLM call rating the run (final output + transcript excerpt)
  against the dimension's rubric, returning a clamped 0–1.
- `judge` is an **injectable seam** — `(prompt) -> float` (or a small object). Tests pass a fake
  judge returning canned scores, so scorer/evalrun tests spend nothing.
- The default judge implementation reuses the Agent SDK (lazily imported, like the harness's
  default runner) and is excluded from unit coverage; the live smoke test exercises it.

The judge scores only **judge dimensions**; rule dimensions never touch it.

---

## 7. Scoring & report (`optimize/scorer.py`)

`score_task(task: EvalTask, run_result: RunResult, cwd: str, judge=None) -> TaskScore`:
1. For each dimension: rule dims → run their checks in `cwd` → fraction passed; judge dims →
   `judge.score_dimension(...)`.
2. `composite` = Σ(weight·score) / Σ(weight).
3. `passed` = `composite >= task.pass_threshold` **and** every `required` dimension scored 1.0.

`TaskScore` — `task_id, dimension_scores: {name: float}, composite: float, passed: bool,
cost_usd: float, duration_ms: int` (cost/time carried from the `RunResult`).

`EvalReport` — aggregates a whole set under one config/approach:
`config_id, tasks: list[TaskScore], composite (mean of task composites), pass_rate,
total_cost_usd, total_duration_ms`. These are exactly the numbers sub-project 3 ranks
candidates on. `EvalReport.to_json(path)` persists it.

---

## 8. Sandbox / isolation (`optimize/sandbox.py`)

`task_sandbox(task: EvalTask) -> context manager yielding cwd`:
- No seed → fresh empty temp dir.
- `seed_dir` → fresh temp dir with the seed dir's contents copied in.
- `seed_repo {path, ref}` → a git worktree created off `path` at `ref` (cheap, shares the
  object store), yielded as cwd; removed on exit (`git worktree remove`).
- Always cleans up on exit (temp dir removed / worktree removed), even on failure.

Split from evalrun so isolation + cleanup are tested independently (copy mode and worktree mode
each get tests; worktree tests use a tiny throwaway git repo in a temp dir — no network, cheap).

---

## 9. Eval runner (`optimize/evalrun.py`)

`run_evalset(evalset, config, *, runner=None, judge=None, out_dir) -> EvalReport`:
1. For each task: open `task_sandbox(task)` → `cwd`.
2. Build `Task{id, prompt, cwd}`; `harness.run(task, config, runner)` → accurate `RunResult`.
3. `scorer.score_task(task, run_result, cwd, judge)` (rule checks run in `cwd`; judge over the run).
4. Write each run's `RunResult.to_jsonl(out_dir/<task>-<config>.jsonl)` so `tokencast.py report`
   still works; collect `TaskScore`s.
5. Aggregate → `EvalReport`; write `out_dir/report.json`.

Fully testable zero-spend: inject a fake runner (returns a canned `RunResult` and writes the
expected files into `cwd`) + a fake judge. Real `checks` and `sandbox` run actual shell/git in
temp dirs (cheap, no LLM).

---

## 10. Cold-start generator (`optimize/generate.py`)

`generate_evalset(context: str, generator=None) -> EvalSet`:
- Gathers `context` (CLAUDE.md + a shallow repo file listing).
- The default `generator` makes one LLM call drafting tasks + dimensions + checks + weights;
  **injectable** (tests use a fake generator returning a canned EvalSet).
- The CLI writes the result to `evalset.yaml` as a **human-review draft** and prints a loud
  notice: generated rule checks are LLM-authored shell commands — review before running.

---

## 11. CLI (`optimize/cli.py` extension)

- `tokencast-optimize eval run EVALSET --config DIR [--out runs/] [--history PATH] [--yes]`
  - Pre-flight: estimate **total** cost ≈ per-task p90 (reuse Task 7 `estimate_cost`) × N tasks;
    print it and confirm before spend unless `--yes`.
  - Runs the set, prints the aggregate report (composite, pass_rate, total cost/time) and the
    `report.json` path.
- `tokencast-optimize eval init [--context CLAUDE.md] [--out evals/generated/]`
  - Writes the draft eval set + the review notice.

---

## 12. Safety

- Checks run shell commands **inside the isolated sandbox** (temp dir / throwaway worktree),
  never the user's working repo directly. The worktree is a separate checkout; cleanup is
  guaranteed on exit.
- **Generated eval sets are drafts requiring human review** before `eval run` — the rule
  commands were written by an LLM. The `eval init` output and docs say so loudly.
- Command checks run under a timeout to bound runaway processes.

---

## 13. Testing

- `evalset`, `checks`, `scorer`, `sandbox`, `evalrun` fully unit-tested with **zero spend**
  (fake runner + fake judge; real shell/git in temp dirs for checks/sandbox).
- `generate` tested with a fake generator.
- One opt-in live smoke test gated by `TOKENCAST_LIVE=1`: generate (or load) a tiny one-task
  eval set, run it under a haiku config with a small budget, assert a real `EvalReport` with a
  composite in [0,1] and accurate cost.

---

## 14. Out of scope (later sub-projects)

- Candidate generation, ranking, Pareto selection, promote/deploy → sub-project 3.
- The choice of *which* knob to optimize (config vs how-to-run-this-task) → sub-project 3.
- Model/instruction/skills/MCP optimization strategies → sub-project 4.
- Task decomposition / multi-model routing → sub-project 5.
- The conversational `/tokencast-optimize` skill front door → sub-project 6.

## 15. Success criteria

- A hand-authored `evalset.yaml` (including a **single-task** set and an **existing-repo
  seeded** task) runs end-to-end and yields an `EvalReport` with accurate per-task cost/time,
  per-dimension scores, composite, and pass/fail.
- `required` rule gates correctly fail a task whose code doesn't run, regardless of composite.
- `eval init` drafts a reviewable `evalset.yaml` from the repo/CLAUDE.md.
- The whole suite passes with zero API spend and no `claude-agent-sdk` installed.
- Each run's JSONL is consumable by the unchanged `tokencast.py report`.
