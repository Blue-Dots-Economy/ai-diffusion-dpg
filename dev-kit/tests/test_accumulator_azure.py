"""Tests for accumulator.declare_azure_needed and get_required_secrets."""
from dev_kit.agent.accumulator import ConfigAccumulator


# ---------------------------------------------------------------------------
# declare_azure_needed
# ---------------------------------------------------------------------------

def test_declare_azure_needed_sets_flag():
    acc = ConfigAccumulator()
    acc.declare_azure_needed()
    assert acc._data["azure_storage"]["needed"] is True


def test_declare_azure_needed_no_credentials_stored():
    acc = ConfigAccumulator()
    acc.declare_azure_needed()
    # Must NOT store account_name, account_key, or container_name
    stored = acc._data["azure_storage"]
    assert "account_name" not in stored
    assert "account_key" not in stored
    assert "container_name" not in stored


def test_declare_azure_needed_is_idempotent():
    acc = ConfigAccumulator()
    acc.declare_azure_needed()
    acc.declare_azure_needed()
    assert acc._data["azure_storage"]["needed"] is True


def test_is_azure_needed_false_by_default():
    acc = ConfigAccumulator()
    assert acc.is_azure_needed() is False


def test_is_azure_needed_true_after_declare():
    acc = ConfigAccumulator()
    acc.declare_azure_needed()
    assert acc.is_azure_needed() is True


# ---------------------------------------------------------------------------
# get_required_secrets
# ---------------------------------------------------------------------------

def test_get_required_secrets_empty_when_no_tools():
    acc = ConfigAccumulator()
    assert acc.get_required_secrets() == []


def test_get_required_secrets_empty_when_tools_have_no_auth():
    acc = ConfigAccumulator()
    acc.add_action_gateway_tool({
        "id": "weather",
        "type": "rest_api",
        "auth": {"type": "none"},
    })
    assert acc.get_required_secrets() == []


def test_get_required_secrets_returns_entry_for_api_key_tool():
    acc = ConfigAccumulator()
    acc.add_action_gateway_tool({
        "id": "onest_jobs",
        "type": "rest_api",
        "description": "Search ONEST job listings",
        "auth": {"type": "api_key", "header": "X-API-KEY", "secret_env": "ONEST_API_KEY"},
    })
    secrets = acc.get_required_secrets()
    assert len(secrets) == 1
    assert secrets[0]["env_var"] == "ONEST_API_KEY"
    assert secrets[0]["tool_id"] == "onest_jobs"
    assert secrets[0]["description"] == "Search ONEST job listings"


def test_get_required_secrets_returns_one_entry_per_tool_with_auth():
    acc = ConfigAccumulator()
    acc.add_action_gateway_tool({
        "id": "tool_a",
        "type": "rest_api",
        "description": "Tool A",
        "auth": {"type": "api_key", "secret_env": "TOOL_A_KEY"},
    })
    acc.add_action_gateway_tool({
        "id": "tool_b",
        "type": "rest_api",
        "description": "Tool B",
        "auth": {"type": "none"},
    })
    acc.add_action_gateway_tool({
        "id": "tool_c",
        "type": "rest_api",
        "description": "Tool C",
        "auth": {"type": "bearer", "secret_env": "TOOL_C_TOKEN"},
    })
    secrets = acc.get_required_secrets()
    env_vars = [s["env_var"] for s in secrets]
    assert env_vars == ["TOOL_A_KEY", "TOOL_C_TOKEN"]


def test_get_required_secrets_returns_deep_copy():
    acc = ConfigAccumulator()
    acc.add_action_gateway_tool({
        "id": "tool_a",
        "type": "rest_api",
        "description": "Tool A",
        "auth": {"type": "api_key", "secret_env": "TOOL_A_KEY"},
    })
    secrets1 = acc.get_required_secrets()
    secrets1[0]["env_var"] = "MUTATED"
    secrets2 = acc.get_required_secrets()
    assert secrets2[0]["env_var"] == "TOOL_A_KEY"
