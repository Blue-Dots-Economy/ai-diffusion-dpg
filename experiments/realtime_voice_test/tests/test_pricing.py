"""Tests for pricing.py: per-turn cost computation."""
from pricing import RATES_PER_1M, compute_turn_cost


def test_rates_include_required_keys():
    """The rates table has all five token-type rates."""
    required = {
        "input_text", "input_audio", "input_cached",
        "output_text", "output_audio",
    }
    assert required.issubset(set(RATES_PER_1M.keys()))


def test_zero_usage_costs_zero():
    """Zero tokens = zero cost."""
    usage = {
        "input_text_tokens": 0,
        "input_audio_tokens": 0,
        "input_cached_tokens": 0,
        "output_text_tokens": 0,
        "output_audio_tokens": 0,
    }
    assert compute_turn_cost(usage) == 0.0


def test_one_million_each_sums_rates():
    """1M of each token type should sum to the rate total."""
    usage = {
        "input_text_tokens":   1_000_000,
        "input_audio_tokens":  1_000_000,
        "input_cached_tokens": 1_000_000,
        "output_text_tokens":  1_000_000,
        "output_audio_tokens": 1_000_000,
    }
    expected = sum(RATES_PER_1M.values())
    assert compute_turn_cost(usage) == expected


def test_realistic_turn_cost():
    """A turn with 30 text + 100 audio input, 20 text + 80 audio output
    produces a positive cost in the expected order of magnitude (cents)."""
    usage = {
        "input_text_tokens":   30,
        "input_audio_tokens":  100,
        "input_cached_tokens": 0,
        "output_text_tokens":  20,
        "output_audio_tokens": 80,
    }
    cost = compute_turn_cost(usage)
    assert 0.0 < cost < 0.01  # less than a cent per turn at realistic sizes


def test_missing_key_treated_as_zero():
    """Missing token-type key is treated as zero, not an error."""
    usage = {"output_audio_tokens": 100}
    cost = compute_turn_cost(usage)
    # Only output_audio counted: 100 × rate / 1_000_000
    expected = 100 * RATES_PER_1M["output_audio"] / 1_000_000
    assert cost == expected
