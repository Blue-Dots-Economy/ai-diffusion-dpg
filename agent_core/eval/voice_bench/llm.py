# agent_core/eval/voice_bench/llm.py
"""JSON-mode LLM client used by the caller and the judge (OpenAI; key from env, never logged)."""
from __future__ import annotations

import json
from typing import Protocol


class JsonLLM(Protocol):
    def complete_json(self, system: str, user: str, seed: int) -> dict: ...


class OpenAIJsonLLM:
    """OpenAI chat completions in JSON mode.

    Args:
        model: Model id, e.g. gpt-4.1.
        temperature: Sampling temperature.
    """

    def __init__(self, model: str, temperature: float) -> None:
        from openai import OpenAI
        self._client, self.model, self._t = OpenAI(), model, temperature

    def complete_json(self, system: str, user: str, seed: int) -> dict:
        """Return the parsed JSON object; {} when the model returns non-JSON."""
        r = self._client.chat.completions.create(
            model=self.model, temperature=self._t, seed=seed, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        try:
            out = json.loads(r.choices[0].message.content or "{}")
            return out if isinstance(out, dict) else {}
        except ValueError:
            return {}
