"""Apply TokenCast's pricing (live-refreshable) to accurate per-model token counts.

This is the only cost arithmetic in the optimizer tier; it reuses tokencast.PRICING
so the two tiers can never disagree on dollars.
"""
import tokencast


def cost_from_model_usage(model_usage):
    """model_usage: {model_name: {input_tokens, output_tokens,
    cache_creation_input_tokens, cache_read_input_tokens}} -> USD float."""
    total = 0.0
    for model, u in (model_usage or {}).items():
        p, _ = tokencast.price_for(model)
        inp = u.get("input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        total += (inp * p["input"]
                  + cw * p["input"] * tokencast.CACHE_WRITE_MULT
                  + cr * p["input"] * tokencast.CACHE_READ_MULT
                  + out * p["output"]) / 1_000_000.0
    return total
