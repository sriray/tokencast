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
