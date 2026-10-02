"""
agent_core/src/output/contract.py
Renders a channel's output_contract (Spec D §3) into the static prompt block
and picks the contract language for a turn. Pure. Belongs to the Agent Core
DPG block.
"""
from __future__ import annotations

_SCRIPT_LINE = {"devanagari": "Write in Devanagari script.", "latin": "Write in Latin script."}
_NUMBERS_LINE = {"words": "Write every number in words, never as digits."}


def _language_lines(entry: dict) -> list[str]:
    lines: list[str] = []
    if entry.get("script") in _SCRIPT_LINE:
        lines.append(_SCRIPT_LINE[entry["script"]])
    if entry.get("numbers") in _NUMBERS_LINE:
        lines.append(_NUMBERS_LINE[entry["numbers"]])
    lines += [str(r).strip() for r in (entry.get("rules") or []) if str(r).strip()]
    return [f"- {line}" for line in lines]


def render_output_contract(contract: dict | None) -> str:
    """Render ``<output_contract>`` text: default language first, then one group per other language.

    Args:
        contract: Raw ``channels.<name>.output_contract`` dict, or None.

    Returns:
        Block text (static per channel, prompt-cacheable); "" when no contract.
    """
    if not isinstance(contract, dict) or not isinstance(contract.get("languages"), dict):
        return ""
    langs: dict = contract["languages"]
    default = contract.get("default_language")
    parts: list[str] = []
    if isinstance(langs.get(default), dict):
        parts.append("\n".join(_language_lines(langs[default])))
    for name, entry in langs.items():
        if name == default or not isinstance(entry, dict):
            continue
        body = "\n".join(_language_lines(entry))
        if body:
            parts.append(f"If the conversation is in {name}:\n{body}")
    return "\n\n".join(p for p in parts if p)


def contract_language(contract: dict | None, preference: str | None) -> str:
    """The turn's contract language: the caller's preference when the contract has it, else the default.

    Args:
        contract: Raw output_contract dict, or None.
        preference: Session/profile ``language_preference``, or None.

    Returns:
        Language id, or "" when there is no contract.
    """
    if not isinstance(contract, dict):
        return ""
    langs = contract.get("languages") or {}
    if preference and preference in langs:
        return preference
    return str(contract.get("default_language") or "")


def sentence_language(text: str, contract: dict | None, preference: str | None) -> str:
    """Guard language for one sentence.

    An explicit preference always wins. With none, a sentence with more Latin than
    Devanagari letters is guarded as ``english`` when the contract defines it;
    anything else uses the contract default.

    Args:
        text: The sentence about to be guarded.
        contract: Raw output_contract dict, or None.
        preference: Session/profile ``language_preference``, or None/"".

    Returns:
        Language id, or "" when there is no contract.
    """
    base = contract_language(contract, preference)
    if preference or not isinstance(contract, dict) or "english" not in (contract.get("languages") or {}):
        return base
    latin = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    deva = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    return "english" if latin > deva else base
