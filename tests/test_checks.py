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
