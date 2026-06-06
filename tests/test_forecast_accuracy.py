import json
import os
import types

import tokencast


def _write(path, marked, out=500, files=("a.py",)):
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in files]
    line = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "model": "sonnet", "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}
    if marked:
        line["tokencast_accurate"] = True
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _write_many(d, n, marked):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        _write(os.path.join(d, f"s{i}.jsonl"), marked, out=500 + i * 10)


def test_parse_session_marker_sets_accurate(tmp_path):
    p = tmp_path / "m.jsonl"
    _write(str(p), marked=True)
    assert tokencast.parse_session(str(p))["accurate"] is True


def test_parse_session_no_marker_is_floor(tmp_path):
    p = tmp_path / "u.jsonl"
    _write(str(p), marked=False)
    assert tokencast.parse_session(str(p))["accurate"] is False
