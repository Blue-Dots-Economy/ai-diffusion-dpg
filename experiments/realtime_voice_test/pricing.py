"""gpt-realtime-mini per-1M token rates + per-turn cost computation.

Rates are best-effort placeholders. Verify against OpenAI's pricing page
before quoting cost numbers externally:
  https://developers.openai.com/api/docs/pricing
"""
from __future__ import annotations


# All rates per 1,000,000 tokens, in USD.
# These are placeholders — confirm against current OpenAI pricing before
# publishing externally.
RATES_PER_1M: dict[str, float] = {
    "input_text":   0.60,    # text input (system prompt, prior transcripts)
    "input_audio":  10.00,   # audio input (user speech)
    "input_cached": 0.30,    # cached portion of input
    "output_text":  2.40,    # text output (internal transcript)
    "output_audio": 20.00,   # audio output (spoken response)
}


def compute_turn_cost(usage: dict[str, int]) -> float:
    """Compute total USD cost for one turn from token-usage dict.

    Args:
        usage: Dict mapping `{kind}_tokens` keys to integer counts. Missing
            keys are treated as zero. Recognised keys:
              - input_text_tokens
              - input_audio_tokens
              - input_cached_tokens
              - output_text_tokens
              - output_audio_tokens

    Returns:
        Total USD cost for this turn.
    """
    total = 0.0
    for kind, rate in RATES_PER_1M.items():
        n = usage.get(f"{kind}_tokens", 0) or 0
        total += n * rate / 1_000_000
    return total
