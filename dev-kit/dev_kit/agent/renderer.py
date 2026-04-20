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


def _strip_status_header(raw: str) -> str:
    """Strip the leading STATUS comment block from a YAML file's raw text.

    Removes all consecutive comment lines at the top of the file so that
    the returned string contains only the YAML body, preserving its original
    formatting exactly.

    Args:
        raw: Full file contents including any STATUS header.

    Returns:
        YAML body with no leading comment lines.
    """
    lines = raw.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return "".join(lines[i:])


def render_block(project_path: Path, block: str, accumulator: ConfigAccumulator) -> None:
    """Write a single block's domain config YAML and update its status in the accumulator.

    Status rules:
    - Empty data → PENDING
    - Draft block (one of the 4 open blocks) with data → DRAFT
    - Non-draft block with data → COMPLETE (agent-generated content is assumed valid)
    - STALE is set externally by the PUT /configs/:block endpoint on validation failure.

    When a YAML file already exists its body is preserved verbatim; only the
    STATUS header comment at the top is added, replaced, or removed.  This
    prevents yaml.dump() from reformatting hand-crafted or imported YAML.

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

    # Strip internal accumulator keys (prefixed with _) before writing.
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    if not data:
        out_path.write_text(f"# {block} — no config generated yet\n")
        accumulator.set_status(block, ConfigStatus.PENDING)
        return

    # Preserve the existing YAML body to avoid reformatting — but only when the
    # file content matches the accumulator data.  If the AI has updated the
    # block since the file was last written, fall through to yaml.dump so the
    # file stays in sync with the accumulator.
    if out_path.exists():
        existing_body = _strip_status_header(out_path.read_text())
        try:
            existing_data = yaml.safe_load(existing_body) or {}
        except yaml.YAMLError:
            existing_data = {}
        if existing_data == data:
            raw_body = existing_body  # content unchanged — keep original formatting
        else:
            raw_body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    else:
        raw_body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    errors = validate_partial(block, data)
    if errors:
        error_lines = "\n".join(f"#   - {e}" for e in errors)
        header = _STALE_HEADER_TPL.format(errors=error_lines)
        out_path.write_text(header + raw_body)
        accumulator.set_status(block, ConfigStatus.STALE)
        return

    if block in DRAFT_BLOCKS:
        out_path.write_text(_DRAFT_HEADER + raw_body)
        accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        out_path.write_text(raw_body)
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
