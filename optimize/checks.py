"""Deterministic checks, run inside a task's sandbox directory. Pure code, no LLM.

Command checks run in a new POSIX session (process group) so a timeout kills the whole
group, not just the shell -- preventing orphaned children from holding the sandbox open.
All check kinds honor a strict "never raises on check failure" contract.
"""
import os
import re
import signal
import subprocess


def _confined(cwd, rel):
    """Resolve `rel` under `cwd`, or return None if it escapes the sandbox. realpath also
    resolves symlinks, so an absolute path, a '..' walk, or an in-sandbox symlink pointing
    out is all caught. check.path can come from an LLM-generated eval set (untrusted)."""
    base = os.path.realpath(cwd)
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def run_check(check, cwd):
    """check: optimize.evalset.Check. Returns True/False. Never raises on check failure."""
    if check.kind == "command":
        return _run_command(check, cwd)
    if check.kind == "file_exists":
        p = _confined(cwd, check.path)
        return bool(p) and os.path.exists(p)
    if check.kind == "file_contains":
        p = _confined(cwd, check.path)
        if not p or not os.path.isfile(p):
            return False
        with open(p, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        try:
            return re.search(check.pattern, text) is not None
        except re.error:
            return False
    return False


def _run_command(check, cwd):
    try:
        proc = subprocess.Popen(
            check.cmd, shell=True, cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except OSError:
        return False
    try:
        proc.communicate(timeout=check.timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.communicate()
        return False
    return proc.returncode == check.expect_exit
