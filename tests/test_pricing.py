from optimize import pricing


def test_cost_from_model_usage_sonnet():
    # Sonnet defaults: input $3/1M, output $15/1M, cache_read 0.10x input.
    usage = {
        "claude-sonnet-4-6": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10000,
        }
    }
    # (1000*3 + 0 + 10000*3*0.10 + 500*15) / 1e6 = (3000 + 3000 + 7500)/1e6
    assert abs(pricing.cost_from_model_usage(usage) - 0.0135) < 1e-9


def test_cost_from_model_usage_empty():
    assert pricing.cost_from_model_usage({}) == 0.0
    assert pricing.cost_from_model_usage(None) == 0.0


def test_cost_sums_across_multiple_models():
    # Opus: (1000*5 + 200*25)/1e6 = 10000/1e6 = 0.01
    # Sonnet: (1000*3*1.25 + 100*15)/1e6 = (3750 + 1500)/1e6 = 0.00525
    usage = {
        "claude-opus-4-8": {"input_tokens": 1000, "output_tokens": 200,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "claude-sonnet-4-6": {"input_tokens": 0, "output_tokens": 100,
                             "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0},
    }
    assert abs(pricing.cost_from_model_usage(usage) - 0.01525) < 1e-9


def test_cache_write_multiplier_applied():
    # Sonnet cache_creation billed at 1.25x input: (1000*3*1.25)/1e6 = 0.00375
    usage = {"claude-sonnet-4-6": {"input_tokens": 0, "output_tokens": 0,
                                   "cache_creation_input_tokens": 1000,
                                   "cache_read_input_tokens": 0}}
    assert abs(pricing.cost_from_model_usage(usage) - 0.00375) < 1e-9


def test_unknown_model_falls_back_to_sonnet():
    # price_for() returns the Sonnet fallback for unrecognized models: (1000*3)/1e6 = 0.003
    usage = {"some-future-model": {"input_tokens": 1000, "output_tokens": 0,
                                   "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 0}}
    assert abs(pricing.cost_from_model_usage(usage) - 0.003) < 1e-9
