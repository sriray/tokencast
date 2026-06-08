"""Per-task isolation. Yields a working dir; always cleans up on exit.

- no seed          -> fresh empty temp dir
- task.seed_dir    -> fresh temp dir with the seed dir's contents copied in
- task.seed_repo   -> git worktree off {path}@{ref} (cheap; shares the object store)
"""
import contextlib
import os
import shutil
import subprocess
import tempfile


@contextlib.contextmanager
def task_sandbox(task):
    if task.seed_repo:
        repo = os.path.abspath(task.seed_repo["path"])
        ref = task.seed_repo.get("ref", "HEAD")
        wt = tempfile.mkdtemp(prefix="tokencast-wt-")
        try:
            try:
                subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", wt, ref],
                               check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"git worktree add failed for {repo}@{ref}: "
                    f"{e.stderr.decode(errors='ignore').strip()}")
            try:
                yield wt
            finally:
                subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt],
                               capture_output=True)
        finally:
            shutil.rmtree(wt, ignore_errors=True)
        return

    d = tempfile.mkdtemp(prefix="tokencast-sbx-")
    try:
        if task.seed_dir:
            src = os.path.abspath(task.seed_dir)
            for name in os.listdir(src):
                s = os.path.join(src, name)
                t = os.path.join(d, name)
                # Preserve symlinks as symlinks (don't dereference): a seed dir is partially
                # untrusted, and a link like leak -> ~/.aws/credentials would otherwise copy
                # the secret's CONTENTS into the agent-readable sandbox.
                if os.path.islink(s):
                    os.symlink(os.readlink(s), t)
                elif os.path.isdir(s):
                    shutil.copytree(s, t, symlinks=True)
                else:
                    shutil.copy2(s, t)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
