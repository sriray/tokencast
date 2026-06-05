import os

from optimize.config import AgentConfig
from optimize.staging import stage_skills


def test_stage_skills_copies_into_cwd(tmp_path):
    src = tmp_path / "cfgskills"
    (src / "myskill").mkdir(parents=True)
    (src / "myskill" / "SKILL.md").write_text("---\ndescription: x\n---\n")
    (src / "myskill" / "helper.py").write_text("x = 1\n")
    cwd = tmp_path / "sandbox"
    cwd.mkdir()

    cfg = AgentConfig(config_id="c", model="sonnet", skills_source=str(src))
    stage_skills(cfg, str(cwd))

    assert os.path.isfile(os.path.join(str(cwd), ".claude", "skills", "myskill", "SKILL.md"))
    assert os.path.isfile(os.path.join(str(cwd), ".claude", "skills", "myskill", "helper.py"))


def test_stage_skills_noop_without_source(tmp_path):
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    stage_skills(AgentConfig(config_id="c", model="sonnet"), str(cwd))   # skills_source None
    assert not os.path.exists(os.path.join(str(cwd), ".claude"))
    # non-existent skills_source is also a no-op
    stage_skills(AgentConfig(config_id="c", model="sonnet", skills_source="/no/such/dir"),
                 str(cwd))
    assert not os.path.exists(os.path.join(str(cwd), ".claude"))
