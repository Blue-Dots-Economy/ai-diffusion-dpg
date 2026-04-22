"""Dev-kit authoring-time renderer for channels.voice.tts_rules.

Canonical source of TTS formatting rules is ``channels.voice.tts_rules`` in
``agent_core.yaml``. Runtime (Agent Core) only reads
``channels.voice.system_prompt_suffix``. This module merges the rules into the
suffix at YAML-write time so the LLM actually sees them, while keeping the
``tts_rules`` block intact for human authoring.

Belongs to the Domain Configuration Kit (dev-kit) block.
"""
from __future__ import annotations

import copy
import re

TTS_BLOCK_BEGIN = "<!-- tts_rules:begin -->"
TTS_BLOCK_END = "<!-- tts_rules:end -->"
_TTS_BLOCK_NOTICE = "(auto-generated from channels.voice.tts_rules; edit tts_rules to change)"

_BLOCK_RE = re.compile(
    re.escape(TTS_BLOCK_BEGIN) + r".*?" + re.escape(TTS_BLOCK_END),
    flags=re.DOTALL,
)

_RULE_LABELS = {
    "numbers": "Numbers",
    "money": "Money",
    "dates": "Dates",
    "time": "Time",
    "phone": "Phone numbers",
    "abbreviations": "Abbreviations",
    "output_script": "Output script",
    "english_loanwords": "English loanwords",
}


def merge_voice_tts_into_suffix(agent_core_config: dict) -> dict:
    """Return a deep-copy of agent_core config with rendered TTS block in the voice suffix.

    The input dict is not mutated. If ``channels.voice.tts_rules`` is missing,
    null, or all values are empty, the suffix is returned unchanged.

    Args:
        agent_core_config: Parsed agent_core.yaml as a dict.

    Returns:
        Deep-copied dict with ``channels.voice.system_prompt_suffix`` rewritten
        to contain the rendered TTS rules between delimited markers.
    """
    out = copy.deepcopy(agent_core_config)
    voice = out.get("channels", {}).get("voice")
    if not isinstance(voice, dict):
        return out

    tts_rules = voice.get("tts_rules")
    rendered = _format_tts_rules(tts_rules)
    suffix = voice.get("system_prompt_suffix", "") or ""
    cleaned = _strip_tts_block(suffix)

    if not rendered:
        voice["system_prompt_suffix"] = cleaned
        return out

    block = f"{TTS_BLOCK_BEGIN}\n{_TTS_BLOCK_NOTICE}\n{rendered}\n{TTS_BLOCK_END}"
    if cleaned.strip():
        voice["system_prompt_suffix"] = f"{cleaned.rstrip()}\n\n{block}"
    else:
        voice["system_prompt_suffix"] = block
    return out


def strip_voice_tts_from_suffix(agent_core_config: dict) -> dict:
    """Return a deep-copy with any rendered TTS block removed from the voice suffix.

    Used when loading a previously-rendered YAML back into the in-memory
    accumulator so the author only sees the prose they authored.

    Args:
        agent_core_config: Parsed agent_core.yaml as a dict.

    Returns:
        Deep-copied dict with the marker-delimited block removed from
        ``channels.voice.system_prompt_suffix``.
    """
    out = copy.deepcopy(agent_core_config)
    voice = out.get("channels", {}).get("voice")
    if not isinstance(voice, dict):
        return out
    suffix = voice.get("system_prompt_suffix")
    if not isinstance(suffix, str) or TTS_BLOCK_BEGIN not in suffix:
        return out
    voice["system_prompt_suffix"] = _strip_tts_block(suffix)
    return out


def _strip_tts_block(suffix: str) -> str:
    """Remove the marker-delimited TTS block from a suffix string.

    Returns the suffix unchanged when no markers are present. Collapses the
    extra blank line the merger inserts so repeated merge→strip cycles are
    stable.
    """
    if TTS_BLOCK_BEGIN not in suffix:
        return suffix
    cleaned = _BLOCK_RE.sub("", suffix)
    return cleaned.rstrip()


def _format_tts_rules(tts_rules: object) -> str:
    """Render a tts_rules dict as a bulleted block for the system prompt.

    Returns an empty string when the input is missing, not a dict, or has no
    non-empty values — callers should skip emitting the delimited block in
    that case.
    """
    if not isinstance(tts_rules, dict):
        return ""
    lines: list[str] = []
    for key, label in _RULE_LABELS.items():
        value = tts_rules.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        lines.append(f"- {label}: {text}")
    if not lines:
        return ""
    return "TTS formatting rules (voice channel):\n" + "\n".join(lines)
