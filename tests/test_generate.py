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


import pytest


def test_gather_context_truncation_marker(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text("x\n")
    ctx = gather_context(root=str(tmp_path), max_files=1)
    assert "truncated at 1 of" in ctx


def test_generate_evalset_rejects_empty_output():
    with pytest.raises(ValueError):
        generate_evalset("ctx", generator=lambda c: {"tasks": []})


def test_generate_evalset_validates_malformed_task():
    bad = {"tasks": [{"id": "t", "prompt": "p", "dimensions": [
        {"name": "d", "rule": {"kind": "file_exists", "path": "a"}, "judge": "both bad"}]}]}
    with pytest.raises(ValueError):
        generate_evalset("ctx", generator=lambda c: bad)
