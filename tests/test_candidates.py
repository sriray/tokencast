from optimize.candidates import from_dirs, model_sweep
from optimize.config import AgentConfig


def test_model_sweep_makes_variants_skipping_baseline_model():
    baseline = AgentConfig(config_id="baseline", model="sonnet")
    variants = model_sweep(baseline)
    ids = sorted(v.config_id for v in variants)
    models = sorted(v.model for v in variants)
    assert ids == ["baseline-haiku", "baseline-opus"]   # sonnet skipped
    assert models == ["haiku", "opus"]
    assert all(v.system_prompt_append == baseline.system_prompt_append for v in variants)


def test_model_sweep_custom_models():
    baseline = AgentConfig(config_id="b", model="opus")
    variants = model_sweep(baseline, models=("opus", "haiku"))
    assert [v.config_id for v in variants] == ["b-haiku"]   # opus == baseline, skipped


def test_from_dirs_loads_configs(tmp_path):
    d = tmp_path / "c1"
    d.mkdir()
    (d / "metadata.yaml").write_text("model: haiku\n")
    configs = from_dirs([str(d)])
    assert len(configs) == 1
    assert configs[0].model == "haiku"
    assert configs[0].config_id == "c1"
