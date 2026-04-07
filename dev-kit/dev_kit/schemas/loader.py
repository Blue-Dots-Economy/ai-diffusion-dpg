"""
dev-kit/dev_kit/schemas/loader.py

Loads YAML template files for each DPG block.

YAML templates (*.yaml) serve as the single source of truth for:
  - Valid field names at every nesting level (shown in agent phase prompts)
  - Structural key validation (generated configs must not contain extra keys)
  - Top-level section names exposed to the update_config tool
"""
from __future__ import annotations

from pathlib import Path

import yaml

_SCHEMAS_DIR = Path(__file__).parent

_TEMPLATE_FILES: dict[str, str] = {
    "agent_core": "agent_core.yaml",
    "knowledge_engine": "knowledge_engine.yaml",
    "memory_layer": "memory_layer.yaml",
    "trust_layer": "trust_layer.yaml",
    "action_gateway": "action_gateway.yaml",
    "reach_layer": "reach_layer.yaml",
    "observability_layer": "observability_layer.yaml",
}

# Caches — loaded once on first access
_template_text_cache: dict[str, str] = {}
_template_dict_cache: dict[str, dict] = {}


def load_template_text(block: str) -> str:
    """Return the YAML template for a block as a raw string (with comments).

    Injected verbatim into agent phase prompts so Claude sees exact field
    names and fills in values only.

    Args:
        block: Block name, e.g. "agent_core" or "trust_layer".

    Returns:
        Raw YAML template string including comments.

    Raises:
        ValueError: If block name is not recognised.
        FileNotFoundError: If the template file is missing.
    """
    if block in _template_text_cache:
        return _template_text_cache[block]

    filename = _TEMPLATE_FILES.get(block)
    if filename is None:
        raise ValueError(
            f"No template registered for block {block!r}. "
            f"Known blocks: {sorted(_TEMPLATE_FILES)}"
        )

    path = _SCHEMAS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")

    text = path.read_text(encoding="utf-8")
    _template_text_cache[block] = text
    return text


def load_template(block: str) -> dict:
    """Return the YAML template for a block as a parsed dict.

    Used for structural key validation — every key in a generated config
    must exist in this template.

    Args:
        block: Block name, e.g. "agent_core".

    Returns:
        Parsed template dict (comments stripped).

    Raises:
        ValueError: If block name is not recognised.
        FileNotFoundError: If the template file is missing.
    """
    if block in _template_dict_cache:
        return _template_dict_cache[block]

    text = load_template_text(block)
    parsed = yaml.safe_load(text) or {}
    _template_dict_cache[block] = parsed
    return parsed


def get_valid_sections(block: str) -> list[str]:
    """Return the top-level section names valid for a block.

    Derived from the YAML template top-level keys.

    Args:
        block: Block name, e.g. "agent_core".

    Returns:
        Sorted list of valid top-level section names.

    Raises:
        ValueError: If block name is not recognised.
    """
    template = load_template(block)
    return sorted(template.keys())
