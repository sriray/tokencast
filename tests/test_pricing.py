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
