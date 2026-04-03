"""
dev-kit/dev_kit/agent/renderer.py

Writes accumulated config values to YAML files in a project directory.
Computes config status based on data presence and block type.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from dev_kit.agent.accumulator import BLOCKS, DRAFT_BLOCKS, ConfigAccumulator, ConfigStatus
from dev_kit.schema import validate_partial

_DRAFT_HEADER = "# STATUS: draft — block template not yet finalized\n"
_STALE_HEADER_TPL = "# STATUS: stale — validation errors detected:\n{errors}\n"


def render_all(project_path: Path, accumulator: ConfigAccumulator) -> dict[str, ConfigStatus]:
    """Write all 7 block config YAML files and return their statuses.

    Args:
        project_path: Absolute path to the project's configs directory.
        accumulator: Current config accumulator.

    Returns:
        Dict of block name → ConfigStatus after writing.
    """
    project_path.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, ConfigStatus] = {}
    for block in BLOCKS:
        render_block(project_path, block, accumulator)
        statuses[block] = accumulator.get_status(block)
    return statuses


def render_block(project_path: Path, block: str, accumulator: ConfigAccumulator) -> None:
    """Write a single block's domain config YAML and update its status in the accumulator.

    Status rules:
    - Empty data → PENDING
    - Draft block (one of the 4 open blocks) with data → DRAFT
    - Non-draft block with data → COMPLETE (agent-generated content is assumed valid)
    - STALE is set externally by the PUT /configs/:block endpoint on validation failure.

    Args:
        project_path: Absolute path to the project's configs directory.
        block: Block name.
        accumulator: Config accumulator to read from and update status in.
    """
    data = accumulator.get_block(block)
    out_path = project_path / f"{block}.yaml"

    if not data:
        out_path.write_text(f"# {block} — no config generated yet\n")
        accumulator.set_status(block, ConfigStatus.PENDING)
        return

    yaml_content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    errors = validate_partial(block, data)
    if errors:
        error_lines = "\n".join(f"#   - {e}" for e in errors)
        header = _STALE_HEADER_TPL.format(errors=error_lines)
        out_path.write_text(header + yaml_content)
        accumulator.set_status(block, ConfigStatus.STALE)
        return

    if block in DRAFT_BLOCKS:
        out_path.write_text(_DRAFT_HEADER + yaml_content)
        accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        out_path.write_text(yaml_content)
        accumulator.set_status(block, ConfigStatus.COMPLETE)


def load_block_from_file(project_path: Path, block: str) -> dict:
    """Load a block YAML file back into a dict (for reverse-sync from manual edits).

    Strips the draft header comment before parsing.

    Args:
        project_path: Absolute path to the project's configs directory.
        block: Block name.

    Returns:
        Parsed YAML dict, or empty dict if file does not exist.
    """
    path = project_path / f"{block}.yaml"
    if not path.exists():
        return {}
    raw = path.read_text()
    # Strip comment lines (draft header)
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    return yaml.safe_load("\n".join(lines)) or {}
