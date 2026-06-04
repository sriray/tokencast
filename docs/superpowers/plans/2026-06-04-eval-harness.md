# TokenCast Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the scoring layer on top of the harness: run an eval set (one or many tasks) under a config, score each task with deterministic rule checks + an LLM judge, and produce a composite quality score alongside accurate cost/time.

**Architecture:** New modules under `optimize/` (heavy tier). Each eval task runs in an isolated sandbox (fresh temp dir; seeded by copy or git worktree), is executed via the existing `harness.run`, and scored by combining `checks` (deterministic) with a `judge` (LLM, behind an injectable seam). A cold-start `generate` drafts an eval set from repo context. The CLI gains `eval run` and `eval init`. Everything that spends money is behind an injectable seam, so the whole suite runs with zero API spend and no SDK installed.

**Tech Stack:** Python 3.8+, `pyyaml`, `pytest`; `claude-agent-sdk` only via lazily-imported default seams. The light tier (`tokencast.py`) stays stdlib-only and untouched.

**Reference spec:** `docs/superpowers/specs/2026-06-04-eval-harness-design.md`

**Environment note:** the working interpreter is **`python3.11`** (bare `python`/`python3` lack pytest here). Use `python3.11 -m pytest ...` for all test runs.

**Commit note:** per the repo's global convention, end each commit message with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer (commands below show the subject line; add the trailer).

**Reused from sub-project 1 (do NOT modify):** `optimize/harness.py` (`run(task, config, runner=None)`), `optimize/result.py` (`RunResult`, `.to_jsonl`), `optimize/config.py` (`AgentConfig`), `optimize/pricing.py`, and `optimize/cli.py` (`estimate_cost`, `_fmt_cost`, `cmd_run`, `main`).

---

## File Structure

- Create: `optimize/evalset.py` — `Check`, `Dimension`, `EvalTask`, `EvalSet` (model + YAML load/save + validation).
- Create: `optimize/checks.py` — `run_check(check, cwd) -> bool`.
- Create: `optimize/sandbox.py` — `task_sandbox(task)` context manager (empty / copy / worktree).
- Create: `optimize/judge.py` — `score_dimension(run_result, dimension, judge=None) -> float` (injectable seam).
- Create: `optimize/scorer.py` — `TaskScore`, `EvalReport`, `score_task(...)`.
- Create: `optimize/evalrun.py` — `run_evalset(evalset, config, *, runner, judge, out_dir) -> EvalReport`.
- Create: `optimize/generate.py` — `gather_context(...)`, `generate_evalset(context, generator=None) -> EvalSet`.
- Modify: `optimize/cli.py` — add `eval run` / `eval init` subcommands.
- Create tests: `tests/test_evalset.py`, `tests/test_checks.py`, `tests/test_sandbox.py`, `tests/test_judge.py`, `tests/test_scorer.py`, `tests/test_evalrun.py`, `tests/test_generate.py`, `tests/test_cli_eval.py`, and add to `tests/test_live_smoke.py`.
- Modify docs: `README.md`, `ROADMAP.md`, `CLAUDE.md`.

Untouched: `tokencast.py`, `tokencast.html`.

---

### Task 1: evalset model — Check + Dimension

**Files:**
- Create: `optimize/evalset.py`
- Test: `tests/test_evalset.py`

- [ ] **Step 1: Write the failing test**

`tests/test_evalset.py`:
```python
import pytest

from optimize.evalset import Check, Dimension


def test_check_from_dict_command():
    c = Check.from_dict({"kind": "command", "cmd": "pytest", "expect_exit": 0})
    assert c.kind == "command"
    assert c.cmd == "pytest"
    assert c.expect_exit == 0


def test_check_from_dict_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown check kind"):
        Check.from_dict({"kind": "nope"})


def test_check_from_dict_requires_fields():
    with pytest.raises(ValueError, match="cmd"):
        Check.from_dict({"kind": "command"})
    with pytest.raises(ValueError, match="path"):
        Check.from_dict({"kind": "file_exists"})
    with pytest.raises(ValueError, match="pattern"):
        Check.from_dict({"kind": "file_contains", "path": "a.py"})


def test_dimension_rule_single_and_list():
    d1 = Dimension.from_dict({"name": "t", "weight": 2,
                              "rule": {"kind": "file_exists", "path": "a.py"}})
    assert d1.is_rule and len(d1.checks) == 1 and d1.weight == 2.0
    d2 = Dimension.from_dict({"name": "t", "rule": [
        {"kind": "file_exists", "path": "a.py"},
        {"kind": "file_exists", "path": "b.py"}]})
    assert len(d2.checks) == 2


def test_dimension_judge():
    d = Dimension.from_dict({"name": "clarity", "weight": 2, "judge": "Is it clear? 0-1"})
    assert not d.is_rule
    assert d.judge == "Is it clear? 0-1"


def test_dimension_requires_exactly_one_of_rule_or_judge():
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x"})
    with pytest.raises(ValueError, match="exactly one"):
        Dimension.from_dict({"name": "x", "rule": {"kind": "file_exists", "path": "a"},
                             "judge": "y"})


def test_dimension_required_on_judge_warns():
    with pytest.warns(UserWarning, match="required"):
        Dimension.from_dict({"name": "x", "required": True, "judge": "y"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_evalset.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.evalset'`

- [ ] **Step 3: Write minimal implementation**

`optimize/evalset.py`:
```python
"""EvalSet data model: tasks, dimensions, checks. Loads/saves YAML with loud validation.

A unified dimension model: each Dimension has a weight and is scored EITHER by rule checks
OR by an LLM judge rubric (exactly one).
"""
import os
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_CHECK_KINDS = ("command", "file_exists", "file_contains")


@dataclass
class Check:
    kind: str
    cmd: Optional[str] = None
    expect_exit: int = 0
    path: Optional[str] = None
    pattern: Optional[str] = None
    timeout: int = 120

    @classmethod
    def from_dict(cls, d):
        kind = d.get("kind")
        if kind not in _CHECK_KINDS:
            raise ValueError(f"unknown check kind: {kind!r} (expected one of {_CHECK_KINDS})")
        if kind == "command" and not d.get("cmd"):
            raise ValueError("command check requires 'cmd'")
        if kind in ("file_exists", "file_contains") and not d.get("path"):
            raise ValueError(f"{kind} check requires 'path'")
        if kind == "file_contains" and not d.get("pattern"):
            raise ValueError("file_contains check requires 'pattern'")
        return cls(kind=kind, cmd=d.get("cmd"), expect_exit=int(d.get("expect_exit", 0)),
                   path=d.get("path"), pattern=d.get("pattern"),
                   timeout=int(d.get("timeout", 120)))

    def to_dict(self):
        out = {"kind": self.kind}
        if self.kind == "command":
            out["cmd"] = self.cmd
            if self.expect_exit != 0:
                out["expect_exit"] = self.expect_exit
        else:
            out["path"] = self.path
            if self.kind == "file_contains":
                out["pattern"] = self.pattern
        return out


@dataclass
class Dimension:
    name: str
    weight: float = 1.0
    required: bool = False
    checks: List[Check] = field(default_factory=list)
    judge: Optional[str] = None

    @property
    def is_rule(self):
        return self.judge is None

    @classmethod
    def from_dict(cls, d):
        name = d.get("name")
        if not name:
            raise ValueError("dimension requires 'name'")
        has_rule = "rule" in d
        has_judge = "judge" in d
        if has_rule == has_judge:
            raise ValueError(f"dimension {name!r} must have exactly one of 'rule' or 'judge'")
        required = bool(d.get("required", False))
        if has_judge and required:
            warnings.warn(
                f"dimension {name!r}: 'required' on a judge dimension is discouraged "
                "(judges rarely score exactly 1.0)", UserWarning)
        checks = []
        if has_rule:
            rule = d["rule"]
            items = rule if isinstance(rule, list) else [rule]
            checks = [Check.from_dict(c) for c in items]
        return cls(name=name, weight=float(d.get("weight", 1.0)), required=required,
                   checks=checks, judge=d.get("judge"))

    def to_dict(self):
        out = {"name": self.name, "weight": self.weight}
        if self.required:
            out["required"] = True
        if self.is_rule:
            out["rule"] = [c.to_dict() for c in self.checks]
        else:
            out["judge"] = self.judge
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_evalset.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/evalset.py tests/test_evalset.py
git commit -m "feat(eval): Check + Dimension model with validation"
```

---

### Task 2: evalset model — EvalTask + EvalSet (YAML load/save)

**Files:**
- Modify: `optimize/evalset.py`
- Modify: `tests/test_evalset.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evalset.py`:
```python
from optimize.evalset import EvalSet, EvalTask


def test_evaltask_from_dict_minimal():
    t = EvalTask.from_dict({"id": "t1", "prompt": "do it",
                            "dimensions": [{"name": "d", "judge": "good? 0-1"}]})
    assert t.id == "t1"
    assert t.pass_threshold == 0.6
    assert len(t.dimensions) == 1


def test_evaltask_requires_id_and_prompt():
    with pytest.raises(ValueError, match="id"):
        EvalTask.from_dict({"prompt": "x"})
    with pytest.raises(ValueError, match="prompt"):
        EvalTask.from_dict({"id": "t"})


def test_evaltask_rejects_both_seed_modes():
    with pytest.raises(ValueError, match="mutually exclusive"):
        EvalTask.from_dict({"id": "t", "prompt": "p", "seed_dir": "s",
                            "seed_repo": {"path": "r"}})


def test_evalset_load_roundtrip(tmp_path):
    src = {
        "tasks": [
            {"id": "t1", "prompt": "p1", "pass_threshold": 0.5,
             "dimensions": [
                 {"name": "tests", "weight": 3, "required": True,
                  "rule": {"kind": "command", "cmd": "python x.py", "expect_exit": 0}},
                 {"name": "clarity", "weight": 2, "judge": "clear? 0-1"}]},
        ]
    }
    import yaml
    p = tmp_path / "evalset.yaml"
    p.write_text(yaml.safe_dump(src))

    es = EvalSet.load(str(p))
    assert len(es.tasks) == 1
    t = es.tasks[0]
    assert t.pass_threshold == 0.5
    assert t.dimensions[0].required is True
    assert t.dimensions[0].checks[0].cmd == "python x.py"
    assert t.dimensions[1].judge == "clear? 0-1"

    # save round-trips back to a loadable file with the same shape
    out = tmp_path / "out.yaml"
    es.save(str(out))
    es2 = EvalSet.load(str(out))
    assert es2.tasks[0].dimensions[0].checks[0].cmd == "python x.py"
    assert es2.tasks[0].dimensions[1].judge == "clear? 0-1"


def test_evalset_load_rejects_empty(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text("tasks: []\n")
    with pytest.raises(ValueError, match="no tasks"):
        EvalSet.load(str(p))


def test_evalset_load_wraps_bad_yaml(tmp_path):
    p = tmp_path / "evalset.yaml"
    p.write_text("tasks: [ : bad")
    with pytest.raises(ValueError, match="evalset.yaml"):
        EvalSet.load(str(p))


def test_evalset_single_task_is_fine(tmp_path):
    import yaml
    p = tmp_path / "one.yaml"
    p.write_text(yaml.safe_dump({"tasks": [
        {"id": "solo", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))
    es = EvalSet.load(str(p))
    assert len(es.tasks) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_evalset.py -k "evaltask or evalset" -v`
Expected: FAIL — `ImportError: cannot import name 'EvalSet'`

- [ ] **Step 3: Write minimal implementation**

Append to `optimize/evalset.py`:
```python
@dataclass
class EvalTask:
    id: str
    prompt: str
    pass_threshold: float = 0.6
    dimensions: List[Dimension] = field(default_factory=list)
    seed_dir: Optional[str] = None
    seed_repo: Optional[Dict[str, str]] = None

    @classmethod
    def from_dict(cls, d):
        if not d.get("id"):
            raise ValueError("task requires 'id'")
        if not d.get("prompt"):
            raise ValueError(f"task {d.get('id')!r} requires 'prompt'")
        if d.get("seed_dir") and d.get("seed_repo"):
            raise ValueError(
                f"task {d['id']!r}: seed_dir and seed_repo are mutually exclusive")
        dims = [Dimension.from_dict(x) for x in (d.get("dimensions") or [])]
        return cls(id=d["id"], prompt=d["prompt"],
                   pass_threshold=float(d.get("pass_threshold", 0.6)), dimensions=dims,
                   seed_dir=d.get("seed_dir"), seed_repo=d.get("seed_repo"))

    def to_dict(self):
        out = {"id": self.id, "prompt": self.prompt, "pass_threshold": self.pass_threshold,
               "dimensions": [dim.to_dict() for dim in self.dimensions]}
        if self.seed_dir:
            out["seed_dir"] = self.seed_dir
        if self.seed_repo:
            out["seed_repo"] = self.seed_repo
        return out


@dataclass
class EvalSet:
    tasks: List[EvalTask] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("eval set must be a mapping with a 'tasks' list")
        tasks = [EvalTask.from_dict(t) for t in (data.get("tasks") or [])]
        if not tasks:
            raise ValueError("eval set has no tasks")
        return cls(tasks=tasks)

    @classmethod
    def load(cls, path):
        if yaml is None:
            raise RuntimeError("pyyaml is required; install tokencast[optimize]")
        with open(path, encoding="utf-8") as fh:
            try:
                data = yaml.safe_load(fh) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"failed to parse {path}: {e}")
        try:
            return cls.from_dict(data)
        except ValueError as e:
            raise ValueError(f"{path}: {e}")

    def to_dict(self):
        return {"tasks": [t.to_dict() for t in self.tasks]}

    def save(self, path):
        if yaml is None:
            raise RuntimeError("pyyaml is required; install tokencast[optimize]")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False)
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_evalset.py -v`
Expected: PASS (all evalset tests)

- [ ] **Step 5: Commit**

```bash
git add optimize/evalset.py tests/test_evalset.py
git commit -m "feat(eval): EvalTask + EvalSet with YAML load/save round-trip"
```

---

### Task 3: deterministic checks

**Files:**
- Create: `optimize/checks.py`
- Test: `tests/test_checks.py`

- [ ] **Step 1: Write the failing test**

`tests/test_checks.py`:
```python
from optimize.checks import run_check
from optimize.evalset import Check


def test_command_pass_and_fail(tmp_path):
    ok = Check.from_dict({"kind": "command", "cmd": "exit 0"})
    bad = Check.from_dict({"kind": "command", "cmd": "exit 3"})
    assert run_check(ok, str(tmp_path)) is True
    assert run_check(bad, str(tmp_path)) is False


def test_command_expect_nonzero(tmp_path):
    c = Check.from_dict({"kind": "command", "cmd": "exit 3", "expect_exit": 3})
    assert run_check(c, str(tmp_path)) is True


def test_command_runs_in_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("hi")
    c = Check.from_dict({"kind": "command", "cmd": "test -f marker.txt"})
    assert run_check(c, str(tmp_path)) is True


def test_command_timeout(tmp_path):
    c = Check.from_dict({"kind": "command", "cmd": "sleep 2", "timeout": 1})
    assert run_check(c, str(tmp_path)) is False


def test_file_exists(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert run_check(Check.from_dict({"kind": "file_exists", "path": "a.py"}),
                     str(tmp_path)) is True
    assert run_check(Check.from_dict({"kind": "file_exists", "path": "missing.py"}),
                     str(tmp_path)) is False


def test_file_contains(tmp_path):
    (tmp_path / "a.py").write_text("def slugify(text):\n    pass\n")
    hit = Check.from_dict({"kind": "file_contains", "path": "a.py",
                           "pattern": r"def\s+slugify"})
    miss = Check.from_dict({"kind": "file_contains", "path": "a.py", "pattern": r"def\s+nope"})
    absent = Check.from_dict({"kind": "file_contains", "path": "gone.py", "pattern": "x"})
    assert run_check(hit, str(tmp_path)) is True
    assert run_check(miss, str(tmp_path)) is False
    assert run_check(absent, str(tmp_path)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.checks'`

- [ ] **Step 3: Write minimal implementation**

`optimize/checks.py`:
```python
"""Deterministic checks, run inside a task's sandbox directory. Pure code, no LLM."""
import os
import re
import subprocess


def run_check(check, cwd):
    """check: optimize.evalset.Check. Returns True/False. Never raises on check failure."""
    if check.kind == "command":
        try:
            r = subprocess.run(check.cmd, shell=True, cwd=cwd,
                               capture_output=True, timeout=check.timeout)
        except subprocess.TimeoutExpired:
            return False
        return r.returncode == check.expect_exit
    if check.kind == "file_exists":
        return os.path.exists(os.path.join(cwd, check.path))
    if check.kind == "file_contains":
        p = os.path.join(cwd, check.path)
        if not os.path.isfile(p):
            return False
        with open(p, encoding="utf-8", errors="ignore") as fh:
            return re.search(check.pattern, fh.read()) is not None
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_checks.py -v`
Expected: PASS (6 passed; the timeout test takes ~1s)

- [ ] **Step 5: Commit**

```bash
git add optimize/checks.py tests/test_checks.py
git commit -m "feat(eval): deterministic command/file checks"
```

---

### Task 4: sandbox (isolation)

**Files:**
- Create: `optimize/sandbox.py`
- Test: `tests/test_sandbox.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sandbox.py`:
```python
import os
import subprocess

from optimize.evalset import EvalTask
from optimize.sandbox import task_sandbox


def _task(**kw):
    base = {"id": "t", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}
    base.update(kw)
    return EvalTask.from_dict(base)


def test_empty_sandbox_is_fresh_and_cleaned():
    seen = None
    with task_sandbox(_task()) as cwd:
        seen = cwd
        assert os.path.isdir(cwd)
        assert os.listdir(cwd) == []
    assert not os.path.exists(seen)  # cleaned up


def test_seed_dir_copies_contents(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "start.py").write_text("print('hi')\n")
    (seed / "pkg").mkdir()
    (seed / "pkg" / "mod.py").write_text("x = 1\n")

    with task_sandbox(_task(seed_dir=str(seed))) as cwd:
        assert os.path.isfile(os.path.join(cwd, "start.py"))
        assert os.path.isfile(os.path.join(cwd, "pkg", "mod.py"))


def test_seed_repo_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("V = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=env)

    seen = None
    with task_sandbox(_task(seed_repo={"path": str(repo), "ref": "HEAD"})) as cwd:
        seen = cwd
        assert os.path.isfile(os.path.join(cwd, "tracked.py"))
        assert os.path.abspath(cwd) != os.path.abspath(str(repo))
    assert not os.path.exists(seen)  # worktree removed
    # the worktree is no longer registered
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert seen not in listing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.sandbox'`

- [ ] **Step 3: Write minimal implementation**

`optimize/sandbox.py`:
```python
"""Per-task isolation. Yields a working dir; always cleans up on exit.

- no seed          -> fresh empty temp dir
- task.seed_dir    -> fresh temp dir with the seed dir's contents copied in
- task.seed_repo   -> git worktree off {path}@{ref} (cheap; shares the object store)
"""
import contextlib
import os
import shutil
import subprocess
import tempfile


@contextlib.contextmanager
def task_sandbox(task):
    if task.seed_repo:
        repo = os.path.abspath(task.seed_repo["path"])
        ref = task.seed_repo.get("ref", "HEAD")
        wt = tempfile.mkdtemp(prefix="tokencast-wt-")
        subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", wt, ref],
                       check=True, capture_output=True)
        try:
            yield wt
        finally:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt],
                           capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)
        return

    d = tempfile.mkdtemp(prefix="tokencast-sbx-")
    try:
        if task.seed_dir:
            src = os.path.abspath(task.seed_dir)
            for name in os.listdir(src):
                s = os.path.join(src, name)
                t = os.path.join(d, name)
                if os.path.isdir(s):
                    shutil.copytree(s, t)
                else:
                    shutil.copy2(s, t)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_sandbox.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/sandbox.py tests/test_sandbox.py
git commit -m "feat(eval): task sandbox (empty / copy / git worktree)"
```

---

### Task 5: LLM judge (injectable seam)

**Files:**
- Create: `optimize/judge.py`
- Test: `tests/test_judge.py`

- [ ] **Step 1: Write the failing test**

`tests/test_judge.py`:
```python
from optimize.evalset import Dimension
from optimize.judge import score_dimension
from optimize.result import RunResult


def _run(final="done"):
    return RunResult(task_id="t", config_id="c", model_usage={}, cost_usd=0.0,
                     duration_ms=0, num_turns=1, transcript=[], final_output=final,
                     files_changed=[], accurate=True)


def _dim():
    return Dimension.from_dict({"name": "clarity", "judge": "Is it clear? 0-1"})


def test_score_uses_injected_judge():
    seen = {}

    def fake_judge(prompt):
        seen["prompt"] = prompt
        return 0.8

    assert score_dimension(_run("hello world"), _dim(), judge=fake_judge) == 0.8
    # the rubric and the run's output are surfaced to the judge
    assert "Is it clear?" in seen["prompt"]
    assert "hello world" in seen["prompt"]


def test_score_is_clamped():
    assert score_dimension(_run(), _dim(), judge=lambda p: 1.5) == 1.0
    assert score_dimension(_run(), _dim(), judge=lambda p: -2) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.judge'`

- [ ] **Step 3: Write minimal implementation**

`optimize/judge.py`:
```python
"""LLM judge behind an injectable seam. Scores a judge dimension in [0, 1].

The default judge lazily uses the Agent SDK (excluded from unit coverage); tests inject a
fake judge so scoring runs with zero spend.
"""


def score_dimension(run_result, dimension, judge=None):
    """judge: callable(prompt: str) -> float. Defaults to the live SDK judge."""
    judge = judge or _default_judge
    prompt = _build_prompt(run_result, dimension)
    try:
        score = float(judge(prompt))
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _build_prompt(run_result, dimension):
    out = run_result.final_output or ""
    return (
        "You are scoring an AI coding agent's run on ONE quality dimension.\n"
        f"Dimension: {dimension.name}\n"
        f"Rubric: {dimension.judge}\n\n"
        f"Agent's final output:\n{out[:4000]}\n\n"
        "Respond with ONLY a single number between 0 and 1."
    )


def _default_judge(prompt):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_judge_sdk(prompt))


async def _judge_sdk(prompt):  # pragma: no cover - requires the live SDK
    import re

    from claude_agent_sdk import query, ClaudeAgentOptions

    text = ""
    async for message in query(prompt=prompt, options=ClaudeAgentOptions(model="haiku")):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    text += getattr(block, "text", "")
    m = re.search(r"[0-9]*\.?[0-9]+", text)
    return float(m.group()) if m else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_judge.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/judge.py tests/test_judge.py
git commit -m "feat(eval): LLM judge behind an injectable seam"
```

---

### Task 6: scorer (TaskScore, EvalReport, score_task)

**Files:**
- Create: `optimize/scorer.py`
- Test: `tests/test_scorer.py`

- [ ] **Step 1: Write the failing test**

`tests/test_scorer.py`:
```python
import json

from optimize.evalset import EvalTask
from optimize.result import RunResult
from optimize.scorer import EvalReport, TaskScore, score_task


def _run(cost=0.01, dur=1000):
    return RunResult(task_id="t1", config_id="baseline", model_usage={}, cost_usd=cost,
                     duration_ms=dur, num_turns=1, transcript=[], final_output="done",
                     files_changed=[], accurate=True)


def test_score_task_combines_rule_and_judge(tmp_path):
    (tmp_path / "slug.py").write_text("def slugify(x): return x\n")
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p", "pass_threshold": 0.5,
        "dimensions": [
            {"name": "file", "weight": 1, "rule": {"kind": "file_exists", "path": "slug.py"}},
            {"name": "clarity", "weight": 1, "judge": "clear? 0-1"},
        ]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 0.5)
    # file dim = 1.0, clarity = 0.5, equal weights -> composite 0.75
    assert abs(score.composite - 0.75) < 1e-9
    assert score.dimension_scores["file"] == 1.0
    assert score.dimension_scores["clarity"] == 0.5
    assert score.passed is True
    assert score.cost_usd == 0.01


def test_required_rule_gate_fails_task(tmp_path):
    # required dim's check fails (file absent) -> task fails even if composite is high
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p", "pass_threshold": 0.1,
        "dimensions": [
            {"name": "tests", "weight": 1, "required": True,
             "rule": {"kind": "file_exists", "path": "missing.py"}},
            {"name": "clarity", "weight": 9, "judge": "clear? 0-1"},
        ]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 1.0)
    assert score.dimension_scores["tests"] == 0.0
    assert score.composite >= 0.1   # high because clarity weight dominates
    assert score.passed is False    # but the required gate fails it


def test_rule_dimension_fraction(tmp_path):
    (tmp_path / "a.py").write_text("x")
    task = EvalTask.from_dict({
        "id": "t1", "prompt": "p",
        "dimensions": [
            {"name": "files", "weight": 1, "rule": [
                {"kind": "file_exists", "path": "a.py"},
                {"kind": "file_exists", "path": "b.py"}]}]})
    score = score_task(task, _run(), str(tmp_path), judge=lambda p: 0.0)
    assert score.dimension_scores["files"] == 0.5  # 1 of 2 checks pass


def test_eval_report_aggregates():
    s1 = TaskScore("t1", {"d": 1.0}, composite=0.9, passed=True, cost_usd=0.01, duration_ms=1000)
    s2 = TaskScore("t2", {"d": 0.0}, composite=0.3, passed=False, cost_usd=0.03, duration_ms=3000)
    rep = EvalReport.from_scores("baseline", [s1, s2])
    assert abs(rep.composite - 0.6) < 1e-9
    assert rep.pass_rate == 0.5
    assert abs(rep.total_cost_usd - 0.04) < 1e-9
    assert rep.total_duration_ms == 4000


def test_eval_report_to_json(tmp_path):
    rep = EvalReport.from_scores("baseline", [
        TaskScore("t1", {"d": 1.0}, 1.0, True, 0.01, 1000)])
    p = tmp_path / "report.json"
    rep.to_json(str(p))
    data = json.loads(p.read_text())
    assert data["config_id"] == "baseline"
    assert data["tasks"][0]["task_id"] == "t1"
    assert data["pass_rate"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.scorer'`

- [ ] **Step 3: Write minimal implementation**

`optimize/scorer.py`:
```python
"""Combine deterministic checks + judge into a TaskScore; aggregate into an EvalReport."""
import dataclasses
import json
from dataclasses import dataclass, field
from typing import Dict, List

from optimize import checks as checks_mod
from optimize import judge as judge_mod


@dataclass
class TaskScore:
    task_id: str
    dimension_scores: Dict[str, float]
    composite: float
    passed: bool
    cost_usd: float
    duration_ms: int


@dataclass
class EvalReport:
    config_id: str
    tasks: List[TaskScore] = field(default_factory=list)
    composite: float = 0.0
    pass_rate: float = 0.0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0

    @classmethod
    def from_scores(cls, config_id, scores):
        scores = list(scores)
        n = len(scores) or 1
        return cls(
            config_id=config_id,
            tasks=scores,
            composite=sum(s.composite for s in scores) / n,
            pass_rate=sum(1 for s in scores if s.passed) / n,
            total_cost_usd=sum(s.cost_usd for s in scores),
            total_duration_ms=sum(s.duration_ms for s in scores),
        )

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(self), fh, indent=2)
        return path


def score_task(task, run_result, cwd, judge=None):
    """Score one task's RunResult. Rule checks run in `cwd`; judge dims call the judge seam."""
    dim_scores = {}
    for dim in task.dimensions:
        if dim.is_rule:
            results = [checks_mod.run_check(c, cwd) for c in dim.checks]
            dim_scores[dim.name] = sum(1 for r in results if r) / (len(results) or 1)
        else:
            dim_scores[dim.name] = judge_mod.score_dimension(run_result, dim, judge)

    total_w = sum(d.weight for d in task.dimensions) or 1.0
    composite = sum(d.weight * dim_scores[d.name] for d in task.dimensions) / total_w
    required_ok = all(dim_scores[d.name] >= 1.0 for d in task.dimensions if d.required)
    passed = composite >= task.pass_threshold and required_ok

    return TaskScore(task_id=task.id, dimension_scores=dim_scores, composite=composite,
                     passed=passed, cost_usd=run_result.cost_usd,
                     duration_ms=run_result.duration_ms)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_scorer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/scorer.py tests/test_scorer.py
git commit -m "feat(eval): scorer combines checks + judge into TaskScore/EvalReport"
```

---

### Task 7: eval runner (orchestration)

**Files:**
- Create: `optimize/evalrun.py`
- Test: `tests/test_evalrun.py`

- [ ] **Step 1: Write the failing test**

`tests/test_evalrun.py`:
```python
import json
import os

from optimize.config import AgentConfig
from optimize.evalset import EvalSet
from optimize.evalrun import run_evalset
from optimize.result import RunResult


def _config(tmp_path):
    cfg = tmp_path / "baseline"
    cfg.mkdir()
    (cfg / "metadata.yaml").write_text("model: sonnet\n")
    return AgentConfig.load(str(cfg))


def test_run_evalset_end_to_end(tmp_path):
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "t1", "prompt": "make a.py", "pass_threshold": 0.5,
         "dimensions": [
             {"name": "file", "weight": 1, "rule": {"kind": "file_exists", "path": "a.py"}},
             {"name": "clarity", "weight": 1, "judge": "clear? 0-1"}]}]})

    # Fake runner writes the expected file into the task's sandbox cwd, returns an accurate run.
    def fake_runner(prompt, options, cwd):
        with open(os.path.join(cwd, "a.py"), "w") as fh:
            fh.write("print('hi')\n")
        return {
            "turns": [{"model": "claude-sonnet-4-6", "content": []}],
            "result": {"model_usage": {"claude-sonnet-4-6": {
                "input_tokens": 1000, "output_tokens": 500,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 10000}},
                "num_turns": 1, "duration_ms": 2000, "total_cost_usd": 0.0,
                "result_text": "done"},
        }

    out = tmp_path / "runs"
    report = run_evalset(evalset, _config(tmp_path), runner=fake_runner,
                         judge=lambda p: 1.0, out_dir=str(out))

    assert report.config_id == "baseline"
    assert len(report.tasks) == 1
    assert report.tasks[0].dimension_scores["file"] == 1.0   # runner wrote a.py in the sandbox
    assert report.tasks[0].passed is True
    assert report.tasks[0].cost_usd > 0                      # accurate cost flowed through
    # artifacts written
    assert (out / "report.json").exists()
    assert (out / "t1-baseline.jsonl").exists()
    data = json.loads((out / "report.json").read_text())
    assert data["tasks"][0]["task_id"] == "t1"


def test_run_evalset_single_task_set(tmp_path):
    evalset = EvalSet.from_dict({"tasks": [
        {"id": "solo", "prompt": "p",
         "dimensions": [{"name": "c", "judge": "ok? 0-1"}]}]})

    def fake_runner(prompt, options, cwd):
        return {"turns": [], "result": {
            "model_usage": {"claude-sonnet-4-6": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}},
            "num_turns": 1, "duration_ms": 100, "total_cost_usd": 0.0, "result_text": "x"}}

    report = run_evalset(evalset, _config(tmp_path), runner=fake_runner,
                         judge=lambda p: 0.7, out_dir=str(tmp_path / "runs"))
    assert len(report.tasks) == 1
    assert abs(report.composite - 0.7) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_evalrun.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.evalrun'`

- [ ] **Step 3: Write minimal implementation**

`optimize/evalrun.py`:
```python
"""Run an eval set under one config: sandbox -> harness.run -> score -> EvalReport."""
import os

from optimize import scorer as scorer_mod
from optimize.harness import run as run_task
from optimize.sandbox import task_sandbox


def run_evalset(evalset, config, *, runner=None, judge=None, out_dir="runs"):
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    for task in evalset.tasks:
        with task_sandbox(task) as cwd:
            t = {"id": task.id, "prompt": task.prompt, "cwd": cwd}
            run_result = run_task(t, config, runner=runner)
            run_result.to_jsonl(os.path.join(out_dir, f"{task.id}-{config.config_id}.jsonl"))
            score = scorer_mod.score_task(task, run_result, cwd, judge=judge)
        scores.append(score)
    report = scorer_mod.EvalReport.from_scores(config.config_id, scores)
    report.to_json(os.path.join(out_dir, "report.json"))
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_evalrun.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/evalrun.py tests/test_evalrun.py
git commit -m "feat(eval): run_evalset orchestrates sandbox + harness + scoring"
```

---

### Task 8: cold-start generator

**Files:**
- Create: `optimize/generate.py`
- Test: `tests/test_generate.py`

- [ ] **Step 1: Write the failing test**

`tests/test_generate.py`:
```python
import os

from optimize.generate import gather_context, generate_evalset


def test_gather_context_includes_claude_md_and_files(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Project rules\nBe terse.\n")
    (tmp_path / "main.py").write_text("print(1)\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "util.py").write_text("x = 1\n")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").write_text("nope\n")

    ctx = gather_context(root=str(tmp_path))
    assert "Be terse." in ctx
    assert "main.py" in ctx
    assert os.path.join("pkg", "util.py") in ctx or "pkg/util.py" in ctx
    assert "secret.txt" not in ctx  # hidden dirs skipped


def test_generate_evalset_uses_injected_generator():
    seen = {}

    def fake_generator(context):
        seen["context"] = context
        return {"tasks": [
            {"id": "g1", "prompt": "do x",
             "dimensions": [{"name": "d", "weight": 1, "judge": "ok? 0-1"}]}]}

    es = generate_evalset("some context", generator=fake_generator)
    assert seen["context"] == "some context"
    assert len(es.tasks) == 1
    assert es.tasks[0].id == "g1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.generate'`

- [ ] **Step 3: Write minimal implementation**

`optimize/generate.py`:
```python
"""Cold-start: an LLM drafts an EvalSet from repo context. Injectable generator seam.

The default generator lazily uses the Agent SDK (excluded from unit coverage). The result is
a DRAFT for human review — generated rule checks are LLM-authored shell commands.
"""
import json
import os

from optimize.evalset import EvalSet

_SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".venv", "dist", "build"}


def gather_context(root=".", claude_md="CLAUDE.md", max_files=200):
    parts = []
    cm = os.path.join(root, claude_md)
    if os.path.isfile(cm):
        with open(cm, encoding="utf-8", errors="ignore") as fh:
            parts.append("# CLAUDE.md\n" + fh.read())
    listing = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for f in filenames:
            listing.append(os.path.relpath(os.path.join(dirpath, f), root))
            if len(listing) >= max_files:
                break
        if len(listing) >= max_files:
            break
    parts.append("# Files\n" + "\n".join(sorted(listing)))
    return "\n\n".join(parts)


def generate_evalset(context, generator=None):
    """generator: callable(context: str) -> dict (an eval set as a plain dict).
    Defaults to the live SDK generator. Returns an EvalSet (a DRAFT for human review)."""
    generator = generator or _default_generator
    data = generator(context)
    return EvalSet.from_dict(data)


_PROMPT = (
    "You are drafting an evaluation set for an AI coding agent working in this project.\n"
    "Return ONLY JSON of the form: {\"tasks\": [{\"id\", \"prompt\", \"pass_threshold\", "
    "\"dimensions\": [{\"name\", \"weight\", \"required\"?, "
    "\"rule\": {\"kind\": \"command|file_exists|file_contains\", ...} | \"judge\": \"rubric\"}]}]}.\n"
    "Prefer a deterministic 'tests_pass' command rule (required) plus 1-2 judge dimensions.\n\n"
    "Project context:\n"
)


def _default_generator(context):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_generate_sdk(context))


async def _generate_sdk(context):  # pragma: no cover - requires the live SDK
    import re

    from claude_agent_sdk import query, ClaudeAgentOptions

    text = ""
    async for message in query(prompt=_PROMPT + context[:12000],
                               options=ClaudeAgentOptions(model="sonnet")):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    text += getattr(block, "text", "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group()) if m else {"tasks": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_generate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/generate.py tests/test_generate.py
git commit -m "feat(eval): cold-start eval-set generator behind a seam"
```

---

### Task 9: CLI — `eval run` and `eval init`

**Files:**
- Modify: `optimize/cli.py`
- Test: `tests/test_cli_eval.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_eval.py`:
```python
import json

import yaml

from optimize import cli
from optimize.scorer import EvalReport, TaskScore


def _config(tmp_path):
    cfg = tmp_path / "baseline"
    cfg.mkdir()
    (cfg / "metadata.yaml").write_text("model: sonnet\n")
    return cfg


def test_cmd_eval_run_writes_and_prints(tmp_path, monkeypatch, capsys):
    cfg = _config(tmp_path)
    evalset_path = tmp_path / "evalset.yaml"
    evalset_path.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))

    canned = EvalReport.from_scores("baseline", [
        TaskScore("t1", {"d": 0.9}, composite=0.9, passed=True, cost_usd=0.0049,
                  duration_ms=1500)])
    monkeypatch.setattr(cli, "run_evalset", lambda evalset, config, out_dir: canned)

    import types
    args = types.SimpleNamespace(evalset=str(evalset_path), config=str(cfg),
                                 out=str(tmp_path / "runs"),
                                 history=str(tmp_path / "no_history"), yes=True)
    cli.cmd_eval_run(args)
    out = capsys.readouterr().out
    assert "Composite" in out
    assert "$0.0049" in out          # precise cost
    assert "90%" in out              # pass rate


def test_cmd_eval_run_rejects_missing_config(tmp_path):
    import pytest
    import types
    evalset_path = tmp_path / "evalset.yaml"
    evalset_path.write_text(yaml.safe_dump({"tasks": [
        {"id": "t1", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]}))
    args = types.SimpleNamespace(evalset=str(evalset_path),
                                 config=str(tmp_path / "nope"),
                                 out=str(tmp_path / "runs"),
                                 history=str(tmp_path / "no_history"), yes=True)
    with pytest.raises(SystemExit):
        cli.cmd_eval_run(args)


def test_cmd_eval_init_writes_draft(tmp_path, monkeypatch, capsys):
    from optimize.evalset import EvalSet
    canned = EvalSet.from_dict({"tasks": [
        {"id": "g1", "prompt": "do x", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}]})
    monkeypatch.setattr(cli, "generate_evalset", lambda context: canned)

    import types
    out_dir = tmp_path / "evals" / "generated"
    args = types.SimpleNamespace(root=str(tmp_path), out=str(out_dir))
    cli.cmd_eval_init(args)

    written = out_dir / "evalset.yaml"
    assert written.exists()
    loaded = EvalSet.load(str(written))
    assert loaded.tasks[0].id == "g1"
    err = capsys.readouterr().err
    assert "REVIEW" in err.upper()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_cli_eval.py -v`
Expected: FAIL — `AttributeError: module 'optimize.cli' has no attribute 'cmd_eval_run'`

- [ ] **Step 3: Write the implementation**

In `optimize/cli.py`, add these imports near the existing top-of-file imports (after `from optimize.harness import run as run_task`):
```python
from optimize.evalset import EvalSet
from optimize.evalrun import run_evalset
from optimize.generate import gather_context, generate_evalset
```

Add these two command functions (place them after the existing `cmd_run`):
```python
def cmd_eval_run(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")

    evalset = EvalSet.load(args.evalset)
    config = AgentConfig.load(args.config)
    n_tasks = len(evalset.tasks)

    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        print(f"Pre-flight: {n_tasks} tasks; est. total p90 ~ {_fmt_cost(p90 * n_tasks)} "
              f"(modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)

    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    report = run_evalset(evalset, config, out_dir=args.out)

    print("=" * 60)
    print(f"Eval: {n_tasks} task(s) / config {report.config_id}")
    print(f"  Composite  {report.composite:.2f}    Pass rate  {report.pass_rate * 100:.0f}%")
    print(f"  Cost       {_fmt_cost(report.total_cost_usd)}    "
          f"Duration  {report.total_duration_ms / 1000:.1f}s")
    print(f"  Report     {os.path.join(args.out, 'report.json')}")


def cmd_eval_init(args):
    context = gather_context(root=args.root)
    evalset = generate_evalset(context)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "evalset.yaml")
    evalset.save(path)
    print(f"Draft eval set written to {path}", file=sys.stderr)
    print("REVIEW BEFORE RUNNING: generated rule checks are LLM-authored shell commands.",
          file=sys.stderr)
    print(path)
```

In `main()`, after the existing `r.set_defaults(func=cmd_run)` line and before `args = ap.parse_args()`, add the `eval` subcommand group:
```python
    e = sub.add_parser("eval", help="evaluate a config against an eval set")
    esub = e.add_subparsers(dest="eval_cmd", required=True)

    er = esub.add_parser("run", help="run an eval set under one config, scored")
    er.add_argument("evalset", help="path to an evalset.yaml")
    er.add_argument("--config", required=True, help="path to a config dir")
    er.add_argument("--out", default="./runs", help="directory for run logs + report.json")
    er.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                    help="historical logs for the pre-flight estimate")
    er.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    er.set_defaults(func=cmd_eval_run)

    ei = esub.add_parser("init", help="draft an eval set from the repo (LLM, review before use)")
    ei.add_argument("--root", default=".", help="project root to read context from")
    ei.add_argument("--out", default="evals/generated", help="dir to write the draft evalset.yaml")
    ei.set_defaults(func=cmd_eval_init)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_cli_eval.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite (no regressions to existing CLI tests)**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; 1 skipped live test)

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli_eval.py
git commit -m "feat(eval): tokencast-optimize eval run + eval init"
```

---

### Task 10: docs + opt-in live smoke test

**Files:**
- Modify: `tests/test_live_smoke.py`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add an opt-in live smoke test for the eval path**

Append to `tests/test_live_smoke.py`:
```python
def test_live_eval_run(tmp_path):
    import os as _os

    import pytest as _pytest

    if _os.environ.get("TOKENCAST_LIVE") != "1":
        _pytest.skip("set TOKENCAST_LIVE=1 to run the real-SDK eval smoke test (spends a little)")

    from optimize.config import AgentConfig
    from optimize.evalset import EvalSet
    from optimize.evalrun import run_evalset

    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    config = AgentConfig.load(str(cfg_dir))

    evalset = EvalSet.from_dict({"tasks": [
        {"id": "smoke", "prompt": "Create a file pong.txt containing exactly: pong",
         "pass_threshold": 0.5,
         "dimensions": [
             {"name": "file", "weight": 1,
              "rule": {"kind": "file_exists", "path": "pong.txt"}},
             {"name": "quality", "weight": 1, "judge": "Did it create the file as asked? 0-1"}]}]})

    report = run_evalset(evalset, config, out_dir=str(tmp_path / "runs"))
    assert len(report.tasks) == 1
    assert 0.0 <= report.composite <= 1.0
    assert report.total_cost_usd >= 0.0
```

- [ ] **Step 2: Verify the suite (new live test skipped by default)**

Run: `python3.11 -m pytest -q`
Expected: PASS; live tests skipped (e.g. "NN passed, 2 skipped").

- [ ] **Step 3: Update `README.md`** — extend the "Optimizer tier (preview)" section

Read `README.md`, find the "Optimizer tier (preview)" section added in sub-project 1, and append this subsection at the end of it:
```markdown
### Evaluating configs (eval harness)

Score a config against an eval set (one or many tasks). Each task runs in an isolated sandbox
(fresh temp dir, optionally seeded from a dir or a git worktree), then is scored by deterministic
rule checks (tests pass, file exists) plus an LLM judge for qualitative dimensions:

```bash
# draft an eval set from your repo (LLM; review before running)
tokencast-optimize eval init --root . --out evals/generated/

# run an eval set under a config, scored
tokencast-optimize eval run evals/generated/evalset.yaml --config configs/baseline/ --out runs/
```

`eval run` prints a composite quality score (0–1), pass rate, and total cost/time — the numbers
the optimize loop (next sub-project) ranks candidate configs on. Generated eval sets are drafts:
their rule checks are LLM-authored shell commands, so review them before running.
```

- [ ] **Step 4: Update `ROADMAP.md`** — note sub-project 2 progress

Read `ROADMAP.md`, find section `## 3. Stronger forecast model` (or the nearest roadmap list). Immediately after the `## 1. Fix token accuracy (the blocker)` block's existing content, add a new short paragraph:
```markdown
**Eval harness (sub-project 2) — done.** `optimize/` can now score a config against an eval set
(hybrid: deterministic rule checks + LLM judge), producing a composite quality score alongside
accurate cost/time. This is the precondition for the optimize loop (sub-project 3).
```

- [ ] **Step 5: Update `CLAUDE.md`** — extend the `optimize/` bullet

Read `CLAUDE.md`, find the `optimize/` bullet under `## Current state` added in sub-project 1. Append this sentence to that bullet:
```markdown
  Sub-project 2 adds the eval harness (`evalset`/`checks`/`sandbox`/`judge`/`scorer`/`evalrun`/
  `generate` + `eval run`/`eval init`): score a config against an eval set (rule checks + LLM
  judge) into a composite quality score with accurate cost/time.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python3.11 -m pytest -q`
Expected: PASS (all pass; live tests skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(eval): document eval harness; opt-in live eval smoke test"
```

---

## Definition of done

- `python3.11 -m pytest -q` passes; only the `TOKENCAST_LIVE` smoke tests are skipped.
- `tokencast.py` / `tokencast.html` unchanged.
- A hand-authored `evalset.yaml` (incl. a single-task set and an existing-repo seeded task) runs end-to-end via `tokencast-optimize eval run`, producing per-dimension scores, composite, pass/fail, and accurate cost/time, with `report.json` + per-run JSONL written.
- A required rule gate fails a task whose code doesn't run, regardless of composite.
- `tokencast-optimize eval init` writes a reviewable draft `evalset.yaml`.
- All money-spending paths (runner, judge, generator) are behind injectable seams; the suite runs with no `claude-agent-sdk` installed.
