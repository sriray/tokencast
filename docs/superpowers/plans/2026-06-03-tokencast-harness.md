# TokenCast Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic execution + telemetry substrate (the "harness") that runs a Claude task headlessly via the Agent SDK under a given config and returns accurate cost/time/token measurements.

**Architecture:** A new heavy tier (`optimize/` package, depends on the Agent SDK) layered above the untouched stdlib light tier (`tokencast.py`). The two tiers connect only through a JSONL file in Claude Code's schema — `optimize/` may import `tokencast` (to reuse pricing), but `tokencast.py` never imports `optimize/`. The harness exposes `run(task, config) -> RunResult` with an **injectable runner** so all logic is testable with zero API spend; the real Agent SDK is reached only by the default runner.

**Tech Stack:** Python 3.8+, `claude-agent-sdk` (heavy tier only), `pyyaml` (config files), `pytest` (tests). The light tier stays stdlib-only.

**Reference spec:** `docs/superpowers/specs/2026-06-03-tokencast-harness-design.md`

**Key design invariants (carried from the spec):**
- One config = one model per run (multi-model decomposition is a later sub-project). This makes JSONL cost-reconciliation trivial.
- Accurate token totals come from the SDK result's `model_usage`. The JSONL bridge attaches the **entire** authoritative usage to the **final** assistant turn, so `tokencast.py`'s per-turn sums equal the accurate totals exactly.
- `to_sdk_options()` returns a plain kwargs **dict** (not an SDK object) so `config.py` never imports the SDK and stays unit-testable.

---

## File Structure

- Create: `pyproject.toml` — two-tier package metadata; `optimize` extra pulls the SDK + pyyaml.
- Create: `conftest.py` — puts repo root on `sys.path` so `import tokencast` / `import optimize` work under pytest.
- Create: `optimize/__init__.py` — package marker.
- Create: `optimize/pricing.py` — applies `tokencast` pricing to per-model token counts.
- Create: `optimize/result.py` — `RunResult` dataclass: build from raw run, compute cost, emit JSONL.
- Create: `optimize/config.py` — `AgentConfig`: load a config dir, map to SDK option kwargs.
- Create: `optimize/harness.py` — `run(task, config, runner)` + the default (live SDK) runner.
- Create: `optimize/cli.py` — `tokencast-optimize run` with pre-flight forecast + confirmation.
- Create: `tests/test_pricing.py`, `tests/test_result.py`, `tests/test_config.py`, `tests/test_harness.py`, `tests/test_cli.py`.
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md` (document the new tier).

Untouched: `tokencast.py`, `tokencast.html`.

---

### Task 1: Project scaffolding (two-tier package + test wiring)

**Files:**
- Create: `pyproject.toml`
- Create: `conftest.py`
- Create: `optimize/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create the package marker and test package**

`optimize/__init__.py`:
```python
"""TokenCast optimizer tier (Agent SDK-backed). Depends on claude-agent-sdk.

The light tier (tokencast.py) never imports this package. This package MAY import
tokencast to reuse pricing. The two tiers otherwise communicate only via JSONL files.
"""
```

`tests/__init__.py`:
```python
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "tokencast"
version = "0.2.0"
description = "Forecast and optimize the cost & time of agentic coding tasks"
requires-python = ">=3.8"
dependencies = []

[project.optional-dependencies]
optimize = ["claude-agent-sdk>=0.1.0", "pyyaml>=6.0"]
dev = ["pytest>=7.0", "pyyaml>=6.0"]

[project.scripts]
tokencast-optimize = "optimize.cli:main"

[tool.setuptools]
py-modules = ["tokencast"]
packages = ["optimize"]
```

- [ ] **Step 3: Create `conftest.py`** so tests can import both tiers from the repo root

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

- [ ] **Step 4: Install dev dependencies (pytest + pyyaml; NOT the SDK)**

The test suite needs `pytest` and `pyyaml`. It does **not** need `claude-agent-sdk` — the
harness imports it lazily, so the whole suite runs with zero spend and no SDK installed.

Run: `pip install -e ".[dev]"`
Expected: installs pytest and pyyaml; `tokencast` and `optimize` become importable.

- [ ] **Step 5: Verify the package imports and pytest collects**

Run: `python -c "import tokencast, optimize; print('ok')"`
Expected: prints `ok`

Run: `python -m pytest -q`
Expected: `no tests ran` (exit code 5) — confirms collection works with no failures.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml conftest.py optimize/__init__.py tests/__init__.py
git commit -m "feat(optimize): scaffold two-tier package + test wiring"
```

---

### Task 2: Pricing — dollars from accurate per-model token counts

**Files:**
- Create: `optimize/pricing.py`
- Test: `tests/test_pricing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_pricing.py`:
```python
from optimize import pricing


def test_cost_from_model_usage_sonnet():
    # Sonnet defaults: input $3/1M, output $15/1M, cache_read 0.10x input.
    usage = {
        "claude-sonnet-4-6": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10000,
        }
    }
    # (1000*3 + 0 + 10000*3*0.10 + 500*15) / 1e6 = (3000 + 3000 + 7500)/1e6
    assert abs(pricing.cost_from_model_usage(usage) - 0.0135) < 1e-9


def test_cost_from_model_usage_empty():
    assert pricing.cost_from_model_usage({}) == 0.0
    assert pricing.cost_from_model_usage(None) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.pricing'`

- [ ] **Step 3: Write minimal implementation**

`optimize/pricing.py`:
```python
"""Apply TokenCast's pricing (live-refreshable) to accurate per-model token counts.

This is the only cost arithmetic in the optimizer tier; it reuses tokencast.PRICING
so the two tiers can never disagree on dollars.
"""
import tokencast


def cost_from_model_usage(model_usage):
    """model_usage: {model_name: {input_tokens, output_tokens,
    cache_creation_input_tokens, cache_read_input_tokens}} -> USD float."""
    total = 0.0
    for model, u in (model_usage or {}).items():
        p, _ = tokencast.price_for(model)
        inp = u.get("input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        total += (inp * p["input"]
                  + cw * p["input"] * tokencast.CACHE_WRITE_MULT
                  + cr * p["input"] * tokencast.CACHE_READ_MULT
                  + out * p["output"]) / 1_000_000.0
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pricing.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/pricing.py tests/test_pricing.py
git commit -m "feat(optimize): price runs from accurate per-model token counts"
```

---

### Task 3: RunResult — build from a raw run, compute cost + features

**Files:**
- Create: `optimize/result.py`
- Test: `tests/test_result.py`

The runner (Task 6) produces a normalized `raw` dict of this shape:
```python
{
  "turns": [
    {"model": "claude-sonnet-4-6", "content": [{"type": "text", "text": "..."},
                                                {"type": "tool_use", "name": "Edit",
                                                 "input": {"file_path": "a.py"}}],
     "timestamp": None},
    ...
  ],
  "result": {
    "model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                          "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 10000}},
    "num_turns": 2,
    "duration_ms": 42000,
    "total_cost_usd": 0.013,   # SDK's own estimate; captured but NOT used for our dollars
    "result_text": "done",
  }
}
```

- [ ] **Step 1: Write the failing test**

`tests/test_result.py`:
```python
from optimize.result import RunResult

RAW = {
    "turns": [
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "text", "text": "hi"},
                     {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}},
                     {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
    ],
    "result": {
        "model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                              "cache_creation_input_tokens": 0,
                                              "cache_read_input_tokens": 10000}},
        "num_turns": 2,
        "duration_ms": 42000,
        "total_cost_usd": 0.013,
        "result_text": "done",
    },
}


def test_from_raw_computes_cost_and_features():
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    assert rr.task_id == "t1"
    assert rr.config_id == "baseline"
    assert abs(rr.cost_usd - 0.0135) < 1e-9          # matches pricing test
    assert rr.duration_ms == 42000
    assert rr.num_turns == 2
    assert rr.final_output == "done"
    assert rr.accurate is True
    # a.py appears twice but is deduped; order preserved
    assert rr.files_changed == ["a.py", "b.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_result.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.result'`

- [ ] **Step 3: Write minimal implementation**

`optimize/result.py`:
```python
"""RunResult: the accurate, structured output of one harness run.

Authoritative cost/tokens come from the SDK result's per-model usage. RunResult can
also emit Claude Code-schema JSONL so the untouched tokencast.py can report/forecast
on harness output (the bridge between the two tiers).
"""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

import tokencast
from optimize import pricing

_BASE_EPOCH = 1_700_000_000  # fixed baseline so emitted timestamps are deterministic


@dataclass
class RunResult:
    task_id: str
    config_id: str
    model_usage: Dict[str, Dict[str, int]]
    cost_usd: float
    duration_ms: int
    num_turns: int
    transcript: List[Dict[str, Any]]
    final_output: str
    files_changed: List[str] = field(default_factory=list)
    accurate: bool = True

    @classmethod
    def from_raw(cls, raw, task_id, config_id):
        result = raw.get("result", {}) or {}
        turns = raw.get("turns", []) or []
        model_usage = result.get("model_usage", {}) or {}
        files = []
        for turn in turns:
            for block in turn.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("name") in tokencast.FILE_TOOLS:
                        inp = block.get("input") or {}
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp and fp not in files:
                            files.append(fp)
        return cls(
            task_id=task_id,
            config_id=config_id,
            model_usage=model_usage,
            cost_usd=pricing.cost_from_model_usage(model_usage),
            duration_ms=int(result.get("duration_ms", 0) or 0),
            num_turns=int(result.get("num_turns", len(turns)) or len(turns)),
            transcript=turns,
            final_output=result.get("result_text", "") or "",
            files_changed=files,
            accurate=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_result.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/result.py tests/test_result.py
git commit -m "feat(optimize): RunResult.from_raw computes accurate cost + features"
```

---

### Task 4: RunResult.to_jsonl — the bridge to the light tier (critical)

This is the load-bearing contract: emitted JSONL must read back through `tokencast.parse_session` with **cost equal to `RunResult.cost_usd`**. All authoritative usage is attached to the final assistant turn.

**Files:**
- Modify: `optimize/result.py` (add `to_jsonl` + helpers)
- Modify: `tests/test_result.py` (add the bridge test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_result.py`:
```python
import tokencast


def test_to_jsonl_bridges_to_tokencast(tmp_path):
    rr = RunResult.from_raw(RAW, task_id="t1", config_id="baseline")
    path = tmp_path / "t1-baseline.jsonl"
    rr.to_jsonl(str(path))

    sess = tokencast.parse_session(str(path))
    # The whole point: tokencast's summed cost equals our authoritative cost.
    assert abs(sess["cost"] - rr.cost_usd) < 1e-6
    # Session-level token totals match the authoritative model_usage.
    assert sess["output"] == 500
    assert sess["input"] == 1000
    assert sess["cache_read"] == 10000
    # Features survive: two assistant turns, files deduped, duration in minutes.
    assert sess["assistant_turns"] == 2
    assert sess["files_touched"] == 2
    assert sess["duration_min"] == 0.7  # 42000 ms = 42 s = 0.7 min


def test_to_jsonl_handles_zero_turns(tmp_path):
    raw = {"turns": [], "result": dict(RAW["result"])}
    rr = RunResult.from_raw(raw, task_id="t0", config_id="baseline")
    path = tmp_path / "empty.jsonl"
    rr.to_jsonl(str(path))
    sess = tokencast.parse_session(str(path))
    assert abs(sess["cost"] - rr.cost_usd) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_result.py -k to_jsonl -v`
Expected: FAIL — `AttributeError: 'RunResult' object has no attribute 'to_jsonl'`

- [ ] **Step 3: Write minimal implementation**

Add to the `RunResult` class in `optimize/result.py`:
```python
    def _settlement_usage(self):
        """Sum authoritative per-model usage into one totals dict (single model per run)."""
        totals = {"input_tokens": 0, "output_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        for u in self.model_usage.values():
            for k in totals:
                totals[k] += u.get(k, 0) or 0
        return totals

    def _settlement_model(self):
        return next(iter(self.model_usage), "unknown")

    def to_jsonl(self, path):
        """Write Claude Code-schema JSONL. The entire authoritative usage is attached to
        the FINAL assistant turn, so tokencast.py's per-turn sums equal the accurate
        totals exactly. Earlier turns carry content (for feature extraction) but zero usage.
        """
        import datetime

        def iso(epoch):
            return datetime.datetime.utcfromtimestamp(epoch).isoformat() + "Z"

        zero = {"input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        settle = self._settlement_usage()
        model = self._settlement_model()
        t0 = _BASE_EPOCH
        t1 = _BASE_EPOCH + max(0, self.duration_ms) / 1000.0

        lines = [{"type": "user", "timestamp": iso(t0),
                  "message": {"role": "user", "content": "task"}}]
        turns = self.transcript or [{"model": model, "content": []}]
        n = len(turns)
        for i, turn in enumerate(turns):
            is_last = (i == n - 1)
            lines.append({"type": "assistant", "timestamp": iso(t1 if is_last else t0),
                          "message": {"role": "assistant",
                                      "model": turn.get("model", model),
                                      "content": turn.get("content", []),
                                      "usage": dict(settle) if is_last else dict(zero)}})
        with open(path, "w") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
        return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_result.py -v`
Expected: PASS (all result tests pass, including the two new bridge tests)

- [ ] **Step 5: Commit**

```bash
git add optimize/result.py tests/test_result.py
git commit -m "feat(optimize): emit Claude Code-schema JSONL; cost bridges to tokencast"
```

---

### Task 5: AgentConfig — load a config dir, map to SDK option kwargs

**Files:**
- Create: `optimize/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import json

import yaml

from optimize.config import AgentConfig


def _write_config(d):
    (d / "metadata.yaml").write_text(yaml.safe_dump(
        {"model": "opus", "budget_usd": 2.0, "max_turns": 30}))
    (d / "instructions.md").write_text("Always cite the return policy.\n")
    (d / "tools.json").write_text(json.dumps(
        {"allowed_tools": ["Read", "Edit"], "disallowed_tools": ["Bash"],
         "mcp_servers": {"pw": {"command": "npx"}}}))
    (d / "skills").mkdir()


def test_load_full_config(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    _write_config(cfg_dir)

    cfg = AgentConfig.load(str(cfg_dir))
    assert cfg.config_id == "baseline"
    assert cfg.model == "opus"
    assert cfg.budget_usd == 2.0
    assert cfg.max_turns == 30
    assert cfg.system_prompt_append == "Always cite the return policy."
    assert cfg.allowed_tools == ["Read", "Edit"]
    assert cfg.disallowed_tools == ["Bash"]
    assert cfg.mcp_servers == {"pw": {"command": "npx"}}
    assert cfg.skills_source.endswith("skills")


def test_load_minimal_config_uses_defaults(tmp_path):
    cfg_dir = tmp_path / "min"
    cfg_dir.mkdir()
    cfg = AgentConfig.load(str(cfg_dir))
    assert cfg.config_id == "min"
    assert cfg.model == "sonnet"
    assert cfg.system_prompt_append == ""
    assert cfg.allowed_tools == []
    assert cfg.skills_source is None
    assert cfg.budget_usd is None


def test_to_sdk_options_maps_fields(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    _write_config(cfg_dir)
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()

    assert opts["model"] == "opus"
    assert opts["system_prompt"] == {"type": "preset", "preset": "claude_code",
                                     "append": "Always cite the return policy."}
    assert opts["allowed_tools"] == ["Read", "Edit"]
    assert opts["disallowed_tools"] == ["Bash"]
    assert opts["mcp_servers"] == {"pw": {"command": "npx"}}
    assert opts["max_budget_usd"] == 2.0
    assert opts["max_turns"] == 30
    # Skills staging is finalized in a later sub-project; not mapped to SDK options yet.
    assert "setting_sources" not in opts


def test_to_sdk_options_minimal_is_just_model(tmp_path):
    cfg_dir = tmp_path / "min"
    cfg_dir.mkdir()
    opts = AgentConfig.load(str(cfg_dir)).to_sdk_options()
    assert opts == {"model": "sonnet"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.config'`

- [ ] **Step 3: Write minimal implementation**

`optimize/config.py`:
```python
"""AgentConfig: the mutable unit the optimizer will later mutate. Mirrors Agent
Optimizer's .agent_configs/ layout and maps onto SDK option kwargs.

Directory layout:
    config_dir/
      metadata.yaml   # model, budget_usd, max_turns, config_id
      instructions.md # -> system_prompt append
      tools.json      # -> allowed_tools / disallowed_tools / mcp_servers
      skills/         # -> staged skills dir (mapping finalized in a later sub-project)

to_sdk_options() returns a plain kwargs dict (NOT a ClaudeAgentOptions object) so this
module never imports the SDK and stays unit-testable.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class AgentConfig:
    config_id: str
    model: str = "sonnet"
    system_prompt_append: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=list)
    mcp_servers: Dict[str, Any] = field(default_factory=dict)
    skills_source: Optional[str] = None
    budget_usd: Optional[float] = None
    max_turns: Optional[int] = None

    @classmethod
    def load(cls, path):
        path = os.path.abspath(path)
        default_id = os.path.basename(path.rstrip(os.sep)) or "config"

        meta = {}
        meta_path = os.path.join(path, "metadata.yaml")
        if os.path.exists(meta_path):
            if yaml is None:
                raise RuntimeError(
                    "pyyaml is required to read metadata.yaml; install tokencast[optimize]")
            with open(meta_path) as fh:
                meta = yaml.safe_load(fh) or {}

        instr = ""
        instr_path = os.path.join(path, "instructions.md")
        if os.path.exists(instr_path):
            with open(instr_path) as fh:
                instr = fh.read().strip()

        tools = {}
        tools_path = os.path.join(path, "tools.json")
        if os.path.exists(tools_path):
            with open(tools_path) as fh:
                tools = json.load(fh) or {}

        skills_dir = os.path.join(path, "skills")
        skills_source = skills_dir if os.path.isdir(skills_dir) else None

        return cls(
            config_id=meta.get("config_id", default_id),
            model=meta.get("model", "sonnet"),
            system_prompt_append=instr,
            allowed_tools=tools.get("allowed_tools", []),
            disallowed_tools=tools.get("disallowed_tools", []),
            mcp_servers=tools.get("mcp_servers", {}),
            skills_source=skills_source,
            budget_usd=meta.get("budget_usd"),
            max_turns=meta.get("max_turns"),
        )

    def to_sdk_options(self):
        opts = {"model": self.model}
        if self.system_prompt_append:
            opts["system_prompt"] = {"type": "preset", "preset": "claude_code",
                                     "append": self.system_prompt_append}
        if self.allowed_tools:
            opts["allowed_tools"] = list(self.allowed_tools)
        if self.disallowed_tools:
            opts["disallowed_tools"] = list(self.disallowed_tools)
        if self.mcp_servers:
            opts["mcp_servers"] = dict(self.mcp_servers)
        if self.budget_usd is not None:
            opts["max_budget_usd"] = self.budget_usd
        if self.max_turns is not None:
            opts["max_turns"] = self.max_turns
        # NOTE: skills_source is intentionally NOT mapped to SDK options yet. The exact
        # filesystem-staging mechanism is finalized in the skills-optimization sub-project.
        return opts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/config.py tests/test_config.py
git commit -m "feat(optimize): AgentConfig loads config dir and maps to SDK options"
```

---

### Task 6: Harness — run(task, config, runner) + the default live runner

**Files:**
- Create: `optimize/harness.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing test (fake runner, zero spend)**

`tests/test_harness.py`:
```python
from optimize.config import AgentConfig
from optimize.harness import run

RAW = {
    "turns": [
        {"model": "claude-sonnet-4-6",
         "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}],
         "timestamp": None},
    ],
    "result": {
        "model_usage": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                              "cache_creation_input_tokens": 0,
                                              "cache_read_input_tokens": 10000}},
        "num_turns": 1,
        "duration_ms": 1000,
        "total_cost_usd": 0.0,
        "result_text": "done",
    },
}


def test_run_uses_injected_runner_and_passes_options(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: opus\n")
    config = AgentConfig.load(str(cfg_dir))

    seen = {}

    def fake_runner(prompt, options, cwd):
        seen["prompt"] = prompt
        seen["options"] = options
        seen["cwd"] = cwd
        return RAW

    task = {"id": "task42", "prompt": "do the thing", "cwd": "/tmp/work"}
    result = run(task, config, runner=fake_runner)

    assert seen["prompt"] == "do the thing"
    assert seen["options"]["model"] == "opus"
    assert seen["cwd"] == "/tmp/work"
    assert result.task_id == "task42"
    assert result.config_id == "baseline"
    assert abs(result.cost_usd - 0.0135) < 1e-9
    assert result.files_changed == ["a.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.harness'`

- [ ] **Step 3: Write minimal implementation**

`optimize/harness.py`:
```python
"""The harness: run one task under one config and return an accurate RunResult.

`run` takes an injectable `runner` so all orchestration is testable with zero API
spend. The default runner is the only code that touches the real Agent SDK.
"""
from optimize.result import RunResult


def run(task, config, runner=None):
    """task: {"id": str, "prompt": str, "cwd": str | None}. config: AgentConfig.
    runner: (prompt, options_dict, cwd) -> raw dict. Defaults to the live SDK runner."""
    runner = runner or _default_runner
    options = config.to_sdk_options()
    raw = runner(task["prompt"], options, task.get("cwd"))
    return RunResult.from_raw(raw, task_id=task["id"], config_id=config.config_id)


def _default_runner(prompt, options, cwd):  # pragma: no cover - requires the live SDK
    import asyncio
    return asyncio.run(_run_sdk(prompt, options, cwd))


async def _run_sdk(prompt, options, cwd):  # pragma: no cover - requires the live SDK
    from claude_agent_sdk import query, ClaudeAgentOptions

    if cwd:
        options = dict(options, cwd=cwd)
    sdk_options = ClaudeAgentOptions(**options)

    turns = []
    result = {}
    async for message in query(prompt=prompt, options=sdk_options):
        mtype = type(message).__name__
        if mtype == "AssistantMessage":
            content = []
            for block in getattr(message, "content", []) or []:
                btype = type(block).__name__
                if btype == "TextBlock":
                    content.append({"type": "text", "text": getattr(block, "text", "")})
                elif btype == "ToolUseBlock":
                    content.append({"type": "tool_use",
                                    "name": getattr(block, "name", ""),
                                    "input": getattr(block, "input", {}) or {}})
            turns.append({"model": getattr(message, "model", options.get("model", "")),
                          "content": content, "timestamp": None})
        elif mtype == "ResultMessage":
            raw_mu = getattr(message, "model_usage", {}) or {}
            norm_mu = {}
            for model, u in raw_mu.items():
                if isinstance(u, dict):
                    norm_mu[model] = u
                else:
                    norm_mu[model] = {
                        "input_tokens": getattr(u, "input_tokens", 0) or 0,
                        "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        "cache_creation_input_tokens":
                            getattr(u, "cache_creation_input_tokens", 0) or 0,
                        "cache_read_input_tokens":
                            getattr(u, "cache_read_input_tokens", 0) or 0,
                    }
            result = {
                "model_usage": norm_mu,
                "num_turns": getattr(message, "num_turns", len(turns)),
                "duration_ms": getattr(message, "duration_ms", 0) or 0,
                "total_cost_usd": getattr(message, "total_cost_usd", 0.0) or 0.0,
                "result_text": getattr(message, "result", "") or "",
            }
    return {"turns": turns, "result": result}
```

> **Implementer note:** `_run_sdk` is best-effort against current SDK object shapes and is excluded from coverage. The opt-in live smoke test in Task 8 is what validates it against the real SDK; adjust attribute names there if the SDK differs.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_harness.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add optimize/harness.py tests/test_harness.py
git commit -m "feat(optimize): harness.run with injectable runner + live SDK default"
```

---

### Task 7: CLI — `tokencast-optimize run` with pre-flight forecast + confirmation

**Files:**
- Create: `optimize/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
import json

from optimize import cli
from optimize.result import RunResult


def _write_session(path, output_tokens):
    rec = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
           "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                       "content": [{"type": "text", "text": "x"}],
                       "usage": {"input_tokens": 0, "output_tokens": output_tokens,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 100000}}}
    path.write_text(json.dumps(rec) + "\n")


def test_estimate_cost_returns_p90_with_enough_history(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    for i in range(6):
        _write_session(proj / f"s{i}.jsonl", output_tokens=1000 * (i + 1))
    n, p90 = cli.estimate_cost(str(tmp_path))
    assert n == 6
    assert p90 is not None and p90 > 0


def test_estimate_cost_too_little_history(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_session(proj / "s0.jsonl", output_tokens=1000)
    n, p90 = cli.estimate_cost(str(tmp_path))
    assert n == 1
    assert p90 is None


def test_cmd_run_writes_jsonl_and_summary(tmp_path, monkeypatch, capsys):
    # Config dir
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    (cfg_dir / "metadata.yaml").write_text("model: sonnet\n")
    # Task file
    taskfile = tmp_path / "task1.md"
    taskfile.write_text("do the thing")
    out_dir = tmp_path / "runs"

    canned = RunResult(
        task_id="task1", config_id="baseline",
        model_usage={"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 500,
                                           "cache_creation_input_tokens": 0,
                                           "cache_read_input_tokens": 10000}},
        cost_usd=0.0135, duration_ms=1000, num_turns=1,
        transcript=[{"model": "claude-sonnet-4-6", "content": []}],
        final_output="done", files_changed=[], accurate=True)

    monkeypatch.setattr(cli, "run_task", lambda task, config: canned)

    class Args:
        taskfile = str(taskfile)
        config = str(cfg_dir)
        cwd = None
        budget = 1.0
        history = str(tmp_path / "no_history")
        out = str(out_dir)
        yes = True

    cli.cmd_run(Args())

    out_file = out_dir / "task1-baseline.jsonl"
    assert out_file.exists()
    captured = capsys.readouterr()
    assert "$0.01" in captured.out  # cost summary printed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimize.cli'`

- [ ] **Step 3: Write minimal implementation**

`optimize/cli.py`:
```python
"""`tokencast-optimize` CLI. Sub-project 1 ships the `run` command: execute one task
under one config, measured accurately, with a pre-flight cost forecast + confirmation.
"""
import argparse
import os
import sys

import tokencast
from optimize.config import AgentConfig
from optimize.harness import run as run_task


def estimate_cost(history_path):
    """Rough pre-flight: p90 of historical session costs. Returns (n_sessions, p90 | None).
    Richer kNN-matched forecasting arrives with the optimize loop sub-project."""
    sessions = tokencast.load(history_path)
    if len(sessions) < 5:
        return len(sessions), None
    costs = [s["cost"] for s in sessions]
    return len(sessions), tokencast.pct(costs, 0.9)


def cmd_run(args):
    with open(args.taskfile) as fh:
        prompt = fh.read()
    task_id = os.path.splitext(os.path.basename(args.taskfile))[0]
    task = {"id": task_id, "prompt": prompt, "cwd": args.cwd}

    config = AgentConfig.load(args.config)
    if args.budget is not None:
        config.budget_usd = args.budget

    n, p90 = estimate_cost(args.history)
    if p90 is not None:
        print(f"Pre-flight: {n} past sessions; p90 cost ~ {tokencast.money(p90)} "
              f"(modeled at list prices)", file=sys.stderr)
        if config.budget_usd is not None and p90 > config.budget_usd:
            print(f"  ! p90 estimate exceeds budget {tokencast.money(config.budget_usd)}",
                  file=sys.stderr)
    else:
        print(f"Pre-flight: only {n} past sessions (<5); skipping forecast.", file=sys.stderr)

    if not args.yes:
        resp = input("Proceed with run (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    result = run_task(task, config)

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{result.task_id}-{result.config_id}.jsonl")
    result.to_jsonl(out_path)

    print("=" * 60)
    print(f"Task {result.task_id} / config {result.config_id}")
    print(f"  Cost      {tokencast.money(result.cost_usd)}  (accurate token counts)")
    print(f"  Duration  {result.duration_ms / 1000:.1f}s   "
          f"Turns {result.num_turns}   Files {len(result.files_changed)}")
    print(f"  Log       {out_path}")
    print(f"  Inspect   python tokencast.py report {args.out}")


def main():
    ap = argparse.ArgumentParser(prog="tokencast-optimize",
                                 description="TokenCast optimizer (Agent SDK tier)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one task under one config, measured accurately")
    r.add_argument("taskfile", help="text/markdown file whose contents are the task prompt")
    r.add_argument("--config", required=True, help="path to a config dir")
    r.add_argument("--cwd", default=None, help="working directory for the agent")
    r.add_argument("--budget", type=float, default=None,
                   help="hard USD ceiling (maps to SDK max_budget_usd)")
    r.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    r.add_argument("--out", default="./runs", help="directory to write the result JSONL")
    r.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS (all tests across the five files pass)

- [ ] **Step 6: Commit**

```bash
git add optimize/cli.py tests/test_cli.py
git commit -m "feat(optimize): tokencast-optimize run with pre-flight forecast"
```

---

### Task 8: Docs + opt-in live smoke test

**Files:**
- Create: `tests/test_live_smoke.py`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the opt-in live smoke test (skipped by default, no spend in CI)**

`tests/test_live_smoke.py`:
```python
import os

import pytest

from optimize.config import AgentConfig
from optimize.harness import run


@pytest.mark.skipif(os.environ.get("TOKENCAST_LIVE") != "1",
                    reason="set TOKENCAST_LIVE=1 to run the real-SDK smoke test (spends a little)")
def test_live_trivial_run(tmp_path):
    cfg_dir = tmp_path / "baseline"
    cfg_dir.mkdir()
    # Tiny budget so a runaway can't burn money.
    (cfg_dir / "metadata.yaml").write_text("model: haiku\nbudget_usd: 0.10\nmax_turns: 2\n")
    config = AgentConfig.load(str(cfg_dir))

    task = {"id": "smoke", "prompt": "Reply with exactly the word: pong", "cwd": str(tmp_path)}
    result = run(task, config)  # uses the real default runner

    assert result.accurate is True
    assert result.model_usage  # got per-model usage back
    assert result.cost_usd >= 0.0
    assert result.num_turns >= 1
```

- [ ] **Step 2: Verify it is skipped by default**

Run: `python -m pytest tests/test_live_smoke.py -v`
Expected: SKIPPED (1 skipped) — confirms no spend without `TOKENCAST_LIVE=1`.

- [ ] **Step 3: Update `README.md`** — add an "Optimizer tier (preview)" section

Add this section near the end of `README.md` (before any license/footer):
```markdown
## Optimizer tier (preview)

The `forecast`/`report`/`demo` commands above are the stdlib-only, offline core. A separate
**optimizer tier** wraps a locally-running Claude via the Claude Agent SDK to *measure runs
accurately* — the SDK returns real token counts, so this path is not subject to the JSONL
undercount floor that the core warns about.

Install the extra and run one task under one config:

```bash
pip install -e ".[optimize]"        # pulls claude-agent-sdk + pyyaml
tokencast-optimize run task.md --config configs/baseline/ --budget 2.00 --out runs/
python tokencast.py report runs/    # the core reads the accurate logs back
```

A config dir mirrors Agent Optimizer's layout: `metadata.yaml` (model, budget, max_turns),
`instructions.md` (system-prompt append), `tools.json` (allowed/disallowed tools, MCP servers),
and an optional `skills/` dir. Before any real spend the CLI prints a pre-flight cost estimate
and asks you to confirm.

This is sub-project 1 of a planned closed-loop optimizer (eval harness, candidate generation,
model/skill/MCP optimization, task decomposition). See `docs/superpowers/specs/`.
```

- [ ] **Step 4: Update `ROADMAP.md`** — mark item #1 as in progress

Replace the first two lines under `## 1. Fix token accuracy (the blocker)` (the paragraph beginning "The JSONL `input_tokens` placeholder...") by inserting this line immediately after the heading:
```markdown
**Status: in progress.** The optimizer tier (`optimize/`, sub-project 1) drives Claude via the
Agent SDK, whose result carries real aggregated token counts — accurate on every run TokenCast
executes. Runs are written back as JSONL so the core `forecast`/`report` calibrate on accurate
data. (The passive path over pre-existing interactive sessions is still a floor.)

```

- [ ] **Step 5: Update `CLAUDE.md`** — document the two-tier structure

In `CLAUDE.md` under `## Current state`, add this bullet after the `README.md` bullet:
```markdown
- `optimize/` — NEW heavy tier (depends on `claude-agent-sdk`, `pyyaml`). Wraps a local Claude
  via the Agent SDK to run a task under a config and measure it accurately (`tokencast-optimize
  run`). The light tier (`tokencast.py`) never imports this; the two connect only via JSONL.
  Sub-project 1 of a planned closed-loop optimizer — see `docs/superpowers/specs/` and
  `docs/superpowers/plans/`.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `python -m pytest -q`
Expected: PASS (all pass; the live smoke test shows as skipped).

- [ ] **Step 7: Commit**

```bash
git add tests/test_live_smoke.py README.md ROADMAP.md CLAUDE.md
git commit -m "docs(optimize): document optimizer tier; opt-in live smoke test"
```

---

## Definition of done

- `python -m pytest -q` passes; the only skip is the `TOKENCAST_LIVE` smoke test.
- `tokencast.py` and `tokencast.html` are byte-for-byte unchanged.
- `optimize/` imports `tokencast` but `tokencast.py` imports nothing from `optimize/`.
- A `RunResult` written via `to_jsonl` reads back through `tokencast.parse_session` with cost equal to `RunResult.cost_usd`.
- `tokencast-optimize run` executes a task, prints an accurate cost summary, and writes JSONL consumable by `tokencast.py report`.
