import re

SKILL = ".claude/skills/tokencast-optimize/SKILL.md"


def _read():
    with open(SKILL, encoding="utf-8") as fh:
        return fh.read()


def _subcommands(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r'add_parser\(\s*"([a-z][a-z-]*)"', src))


def test_skill_exists_with_frontmatter():
    text = _read()
    assert text.startswith("---")
    frontmatter = text.split("---", 2)[1]
    assert re.search(r"^name:\s*\S+", frontmatter, re.M)
    assert re.search(r"^description:\s*\S+", frontmatter, re.M)


def test_skill_references_only_real_subcommands():
    text = _read()
    opt_cmds = _subcommands("optimize/cli.py")
    light_cmds = _subcommands("tokencast.py")
    # match command tokens on the SAME line only (avoid the frontmatter name: line)
    for m in re.finditer(r"tokencast-optimize[ \t]+([a-z][a-z-]*)", text):
        assert m.group(1) in opt_cmds, f"skill references unknown tokencast-optimize subcommand: {m.group(1)!r}"
    for m in re.finditer(r"tokencast\.py[ \t]+([a-z][a-z-]*)", text):
        assert m.group(1) in light_cmds, f"skill references unknown tokencast.py subcommand: {m.group(1)!r}"
