"""Tests for renderer.runtime_validate using baked-in MergedConfig classes.

When running on the host (no baked schemas), RUNTIME_SCHEMAS is None and
every runtime_validate call should raise RuntimeValidationError.  Tests
that need real schema validation belong to the docker-image test suite.
"""
import pytest

from dev_kit.agent.errors import RuntimeValidationError
from dev_kit.agent.renderer import RUNTIME_SCHEMAS, runtime_validate


def test_runtime_validate_raises_when_schemas_unavailable():
    """Outside docker, RUNTIME_SCHEMAS is None and any call raises."""
    if RUNTIME_SCHEMAS is not None:
        pytest.skip("Baked schemas available — this branch covers host-only behaviour")
    with pytest.raises(RuntimeValidationError) as exc_info:
        runtime_validate("agent_core", {})
    assert exc_info.value.block == "agent_core"


def test_runtime_validate_unknown_block_raises_keyerror():
    """When schemas are available, an unknown block name raises KeyError."""
    if RUNTIME_SCHEMAS is None:
        pytest.skip("Baked schemas not available on host; this test requires docker")
    with pytest.raises(KeyError):
        runtime_validate("does_not_exist", {})


def test_runtime_validate_invalid_yaml_raises_validation_error():
    """When schemas are available, an invalid dict raises RuntimeValidationError."""
    if RUNTIME_SCHEMAS is None:
        pytest.skip("Baked schemas not available on host; this test requires docker")
    # An empty dict will fail trust_layer's required fields.
    with pytest.raises(RuntimeValidationError) as exc_info:
        runtime_validate("trust_layer", {})
    assert exc_info.value.block == "trust_layer"


def test_runtime_validation_error_attributes():
    """RuntimeValidationError exposes block + pydantic_error."""
    cause = ValueError("inner")
    err = RuntimeValidationError("agent_core", cause)
    assert err.block == "agent_core"
    assert err.pydantic_error is cause
    assert "agent_core" in str(err)
    assert "inner" in str(err)
