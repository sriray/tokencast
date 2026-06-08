"""Atomic file writes: write to a temp sibling, then os.replace onto the final path.

A crash, OOM-kill, or Ctrl-C mid-write must never leave a TRUNCATED artifact behind --
optimize.json/auto.json fail to parse on read-back, and a partial run JSONL is silently
read by tokencast.py as a short/under-counted history that skews the forecast. os.replace
is atomic within a filesystem, and the temp file is created in the destination dir so the
rename stays on one filesystem.
"""
import json
import os
import tempfile


def _replace_from_tmp(path, write):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            write(fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def dump_json(obj, path, indent=2):
    """json.dump(obj) onto `path` atomically."""
    return _replace_from_tmp(path, lambda fh: json.dump(obj, fh, indent=indent))


def write_lines(lines, path):
    """Write `lines` (each gets a trailing newline) onto `path` atomically."""
    return _replace_from_tmp(path, lambda fh: fh.writelines(s + "\n" for s in lines))
