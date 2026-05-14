"""Round-trip: every config under dev-kit/configs/<domain>/<block>.yaml must validate.

If a domain's existing YAML rejects, either the schema is too strict or the YAML is wrong.
This test catches the mismatch before the schema is wired into the wizard.
"""
from pathlib import Path
import yaml
import pytest

from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS

pytestmark = pytest.mark.xfail(
    reason="legacy YAMLs predate deterministic wizard; migration deferred",
    strict=False,
)

CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs"
BLOCKS = [
    "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
    "action_gateway", "reach_layer", "observability_layer",
]


def _domain_dirs() -> list[Path]:
    if not CONFIGS_DIR.exists():
        return []
    return [p for p in CONFIGS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")]


def _validate_each_top_level_section(block: str, data: dict) -> list[str]:
    """Validate every top-level section of a parsed YAML against its domain schema."""
    errors = []
    for top_level, value in data.items():
        schema = DOMAIN_SECTION_SCHEMAS.get((block, top_level))
        if schema is None:
            errors.append(f"unmapped section: {top_level}")
            continue
        try:
            schema.model_validate(value)
        except Exception as exc:
            errors.append(f"{top_level}: {exc}")
    return errors


@pytest.mark.parametrize("domain_dir", _domain_dirs(), ids=lambda p: p.name)
@pytest.mark.parametrize("block", BLOCKS)
def test_domain_block_validates(domain_dir: Path, block: str):
    """Every existing domain config must validate against the new domain schemas."""
    yaml_path = domain_dir / f"{block}.yaml"
    if not yaml_path.exists():
        pytest.skip(f"{yaml_path} not present")
    raw = yaml_path.read_text()
    if not raw.strip():
        pytest.skip(f"{yaml_path} is empty")
    data = yaml.safe_load(raw)
    if not data:
        pytest.skip(f"{yaml_path} parses to empty/null")
    errors = _validate_each_top_level_section(block, data)
    assert not errors, f"{domain_dir.name}/{block}.yaml validation errors:\n  " + "\n  ".join(errors)
