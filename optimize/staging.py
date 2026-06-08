"""Stage a config's skills_source dir into a run's sandbox cwd so the Agent SDK can discover
them as project skills (cwd/.claude/skills). Pure filesystem; never touches the SDK."""
import os
import shutil


def stage_skills(config, cwd):
    """Copy config.skills_source/* into cwd/.claude/skills/. No-op if skills_source is unset
    or not a directory."""
    src = config.skills_source
    if not src or not os.path.isdir(src):
        return
    dest = os.path.join(cwd, ".claude", "skills")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dest, name)
        # Preserve symlinks as symlinks (don't dereference) -- a poisoned skills dir could
        # otherwise copy a secret file's contents into the agent-readable sandbox.
        if os.path.islink(s):
            if not os.path.lexists(d):
                os.symlink(os.readlink(s), d)
        elif os.path.isdir(s):
            shutil.copytree(s, d, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
