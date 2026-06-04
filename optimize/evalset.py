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
