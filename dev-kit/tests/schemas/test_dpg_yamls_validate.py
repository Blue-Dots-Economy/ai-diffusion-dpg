"""Round-trip: every dev-kit/dpg/<block>.yaml must validate against its DPG schema."""
from pathlib import Path
import yaml
import pytest

from dev_kit.schemas.validation import validate_dpg_block

DPG_DIR = Path(__file__).parent.parent.parent / "dpg"
BLOCKS = [
    "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
    "action_gateway", "reach_layer", "observability_layer",
]


@pytest.mark.parametrize("block", BLOCKS)
def test_dpg_yaml_validates(block: str):
    """Every framework default YAML must conform to its DPG schema."""
    yaml_path = DPG_DIR / f"{block}.yaml"
    assert yaml_path.exists(), f"missing DPG yaml: {yaml_path}"
    raw = yaml_path.read_text()
    parsed = yaml.safe_load(raw) or {}
    error = validate_dpg_block(block, parsed)
    assert error is None, f"{yaml_path} validation errors:\n{error}"
