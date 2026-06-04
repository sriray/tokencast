import os
import subprocess

from optimize.evalset import EvalTask
from optimize.sandbox import task_sandbox


def _task(**kw):
    base = {"id": "t", "prompt": "p", "dimensions": [{"name": "d", "judge": "ok? 0-1"}]}
    base.update(kw)
    return EvalTask.from_dict(base)


def test_empty_sandbox_is_fresh_and_cleaned():
    seen = None
    with task_sandbox(_task()) as cwd:
        seen = cwd
        assert os.path.isdir(cwd)
        assert os.listdir(cwd) == []
    assert not os.path.exists(seen)  # cleaned up


def test_seed_dir_copies_contents(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "start.py").write_text("print('hi')\n")
    (seed / "pkg").mkdir()
    (seed / "pkg" / "mod.py").write_text("x = 1\n")

    with task_sandbox(_task(seed_dir=str(seed))) as cwd:
        assert os.path.isfile(os.path.join(cwd, "start.py"))
        assert os.path.isfile(os.path.join(cwd, "pkg", "mod.py"))


def test_seed_repo_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("V = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=env)

    seen = None
    with task_sandbox(_task(seed_repo={"path": str(repo), "ref": "HEAD"})) as cwd:
        seen = cwd
        assert os.path.isfile(os.path.join(cwd, "tracked.py"))
        assert os.path.abspath(cwd) != os.path.abspath(str(repo))
    assert not os.path.exists(seen)  # worktree removed
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert seen not in listing


import tempfile

import pytest


def test_seed_dir_copies_dotfiles(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / ".gitignore").write_text("*.pyc\n")
    nested = seed / ".config"
    nested.mkdir()
    (nested / "settings.json").write_text("{}\n")
    with task_sandbox(_task(seed_dir=str(seed))) as cwd:
        assert os.path.isfile(os.path.join(cwd, ".gitignore"))
        assert os.path.isfile(os.path.join(cwd, ".config", "settings.json"))


def test_seed_repo_add_failure_cleans_up(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.py").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True, env=env)

    created = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **k):
        d = real_mkdtemp(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)

    with pytest.raises(RuntimeError, match="worktree add failed"):
        with task_sandbox(_task(seed_repo={"path": str(repo), "ref": "no-such-ref"})):
            pass
    assert created
    assert not os.path.exists(created[0])  # temp dir must not leak on add failure
