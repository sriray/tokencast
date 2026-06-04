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
