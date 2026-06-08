"""EvalSet data model: tasks, dimensions, checks. Loads/saves YAML with loud validation.

A unified dimension model: each Dimension has a weight and is scored EITHER by rule checks
OR by an LLM judge rubric (exactly one).
"""
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
        timeout = int(d.get("timeout", 120))
        if timeout <= 0:
            # timeout=0 makes communicate(timeout=0) fire immediately, so the check can never
            # pass -- a required command rule with it would fail the task unconditionally.
            raise ValueError(f"{kind} check: timeout must be > 0 seconds, got {timeout}")
        return cls(kind=kind, cmd=d.get("cmd"), expect_exit=int(d.get("expect_exit", 0)),
                   path=d.get("path"), pattern=d.get("pattern"), timeout=timeout)

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
        if self.timeout != 120:
            out["timeout"] = self.timeout
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
        rule_val = d.get("rule")
        judge_val = d.get("judge")
        has_rule = rule_val is not None
        has_judge = judge_val is not None
        if has_rule == has_judge:
            raise ValueError(f"dimension {name!r} must have exactly one of 'rule' or 'judge'")
        weight = float(d.get("weight", 1.0))
        if weight < 0:
            raise ValueError(f"dimension {name!r}: weight must be >= 0")
        required = bool(d.get("required", False))
        if has_judge and required:
            warnings.warn(
                f"dimension {name!r}: 'required' on a judge dimension is discouraged "
                "(judges rarely score exactly 1.0)", UserWarning)
        checks = []
        if has_rule:
            items = rule_val if isinstance(rule_val, list) else [rule_val]
            checks = [Check.from_dict(c) for c in items]
            if not checks:
                raise ValueError(f"dimension {name!r}: rule must have at least one check")
        return cls(name=name, weight=weight, required=required,
                   checks=checks, judge=judge_val)

    def to_dict(self):
        out = {"name": self.name, "weight": self.weight}
        if self.required:
            out["required"] = True
        if self.is_rule:
            out["rule"] = [c.to_dict() for c in self.checks]
        else:
            out["judge"] = self.judge
        return out


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
        seed_repo = d.get("seed_repo")
        if seed_repo is not None and not seed_repo.get("path"):
            raise ValueError(f"task {d['id']!r}: seed_repo requires 'path'")
        threshold = float(d.get("pass_threshold", 0.6))
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"task {d['id']!r}: pass_threshold must be between 0 and 1, got {threshold}")
        dims = [Dimension.from_dict(x) for x in (d.get("dimensions") or [])]
        if not dims:
            # A task with no dimensions scores composite 0.0 with required_ok=all([])=True,
            # so pass_threshold 0.0 would silently "pass" a config that did nothing.
            raise ValueError(f"task {d['id']!r}: requires at least one dimension")
        if sum(dim.weight for dim in dims) == 0:
            raise ValueError(f"task {d['id']!r}: dimension weights sum to 0")
        return cls(id=d["id"], prompt=d["prompt"], pass_threshold=threshold,
                   dimensions=dims, seed_dir=d.get("seed_dir"), seed_repo=seed_repo)

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
        seen = set()
        for t in tasks:
            if t.id in seen:
                raise ValueError(f"duplicate task id: {t.id!r}")
            seen.add(t.id)
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
