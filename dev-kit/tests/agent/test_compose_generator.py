"""Tests for dev_kit.agent.deployer.compose_generator.

Covers selective service inclusion / exclusion based on IntakeState fields
(has_kb, has_external_tools, selected_channels) and the per-service tweaks
(pull_policy, container_name removal, REACH_LAYER_WEB_MODE, depends_on pruning).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_kit.agent.deployer.compose_generator import generate_compose
from dev_kit.agent.intake_state import IntakeState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_COMPOSE = (
    Path(__file__).resolve().parents[3]
    / "automation"
    / "docker"
    / "docker-compose.dev.yml"
)


def _make_intake(
    *,
    has_kb: bool = True,
    has_external_tools: bool = True,
    selected_channels: list[str] | None = None,
    is_multi_turn: bool = True,
    needs_persistent_user_data: bool = False,
    is_companion_style: bool = False,
    needs_consent: bool = False,
    has_hitl: bool = False,
    default_language: str = "en",
    supported_languages: list[str] | None = None,
    domain_description: str = "Test domain",
    project_name: str = "test-project",
) -> IntakeState:
    """Build an IntakeState with sensible defaults."""
    return IntakeState(
        has_kb=has_kb,
        has_external_tools=has_external_tools,
        is_multi_turn=is_multi_turn,
        needs_persistent_user_data=needs_persistent_user_data,
        is_companion_style=is_companion_style,
        needs_consent=needs_consent,
        has_hitl=has_hitl,
        selected_channels=selected_channels or ["web", "voice"],
        default_language=default_language,
        supported_languages=supported_languages or ["en"],
        domain_description=domain_description,
        project_name=project_name,
    )


def _parse(yaml_str: str) -> dict:
    """Parse YAML string and return the document dict."""
    return yaml.safe_load(yaml_str)


# ---------------------------------------------------------------------------
# Test 1 — default intake includes all DPG services
# ---------------------------------------------------------------------------

def test_default_intake_includes_all_services():
    """All services (except dev_kit) are present when intake enables everything."""
    intake = _make_intake(has_kb=True, has_external_tools=True, selected_channels=["web", "voice"])
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    services = result["services"]

    expected = {
        "agent_core",
        "knowledge_engine",
        "action_gateway",
        "trust_layer",
        "memory_layer",
        "observability_layer",
        "reach_layer_web",
        "reach_layer_voice",
        "ngrok",
    }
    for svc in expected:
        assert svc in services, f"Expected service {svc!r} to be present"


# ---------------------------------------------------------------------------
# Test 2 — has_kb=False removes knowledge_engine
# ---------------------------------------------------------------------------

def test_has_kb_false_strips_knowledge_engine():
    """knowledge_engine service is absent when has_kb is False."""
    intake = _make_intake(has_kb=False)
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    assert "knowledge_engine" not in result["services"]


# ---------------------------------------------------------------------------
# Test 3 — has_external_tools=False removes action_gateway
# ---------------------------------------------------------------------------

def test_has_external_tools_false_strips_action_gateway():
    """action_gateway service is absent when has_external_tools is False."""
    intake = _make_intake(has_external_tools=False)
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    assert "action_gateway" not in result["services"]


# ---------------------------------------------------------------------------
# Test 4 — voice not selected strips reach_layer_voice and ngrok
# ---------------------------------------------------------------------------

def test_voice_not_selected_strips_voice_and_ngrok():
    """reach_layer_voice and ngrok are absent when 'voice' is not in selected_channels."""
    intake = _make_intake(selected_channels=["web"])
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    services = result["services"]
    assert "reach_layer_voice" not in services, "reach_layer_voice should be stripped"
    assert "ngrok" not in services, "ngrok should be stripped when voice is absent"


# ---------------------------------------------------------------------------
# Test 5 — web not selected sets routing_only mode on reach_layer_web
# ---------------------------------------------------------------------------

def test_web_not_selected_sets_routing_only_mode():
    """reach_layer_web has REACH_LAYER_WEB_MODE=routing_only when 'web' not selected."""
    intake = _make_intake(selected_channels=["voice"])
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    services = result["services"]

    # reach_layer_web is always kept even when web is not selected
    assert "reach_layer_web" in services, "reach_layer_web must always be present"
    env = services["reach_layer_web"].get("environment", [])
    assert "REACH_LAYER_WEB_MODE=routing_only" in env


# ---------------------------------------------------------------------------
# Test 6 — web selected sets full mode
# ---------------------------------------------------------------------------

def test_web_selected_sets_full_mode():
    """reach_layer_web has REACH_LAYER_WEB_MODE=full when 'web' is selected."""
    intake = _make_intake(selected_channels=["web"])
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    env = result["services"]["reach_layer_web"].get("environment", [])
    assert "REACH_LAYER_WEB_MODE=full" in env


# ---------------------------------------------------------------------------
# Test 7 — depends_on references to omitted services are stripped
# ---------------------------------------------------------------------------

def test_depends_on_references_to_omitted_services_are_stripped(tmp_path: Path):
    """depends_on lists in surviving services have removed services pruned out."""
    fake_compose = tmp_path / "docker-compose.yml"
    fake_compose.write_text(
        """
services:
  knowledge_engine:
    image: foo
    depends_on:
      - redis
  agent_core:
    image: bar
    depends_on:
      - knowledge_engine
      - redis
  redis:
    image: redis:7-alpine
"""
    )
    # Disable KB so knowledge_engine is removed
    intake = _make_intake(has_kb=False, selected_channels=["web"])
    result = _parse(generate_compose(intake, "slug", base_compose_path=fake_compose))
    services = result["services"]

    assert "knowledge_engine" not in services
    assert "agent_core" in services
    agent_depends = services["agent_core"].get("depends_on", [])
    assert "knowledge_engine" not in agent_depends, (
        "knowledge_engine must be pruned from agent_core.depends_on"
    )
    assert "redis" in agent_depends, "redis dependency must be retained"


# ---------------------------------------------------------------------------
# Test 8 — dev_kit service is always removed
# ---------------------------------------------------------------------------

def test_dev_kit_service_is_always_removed():
    """dev_kit is never present in the generated compose output."""
    intake = _make_intake()
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    assert "dev_kit" not in result["services"]


# ---------------------------------------------------------------------------
# Test 9 — pull_policy: missing is set on image services
# ---------------------------------------------------------------------------

def test_pull_policy_missing_is_set():
    """Every service with an 'image' key has pull_policy set to 'missing'."""
    intake = _make_intake()
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    for svc_name, svc in result["services"].items():
        if "image" in svc:
            assert svc.get("pull_policy") == "missing", (
                f"Service {svc_name!r} has 'image' but pull_policy != 'missing'"
            )


# ---------------------------------------------------------------------------
# Test 10 — container_name is stripped from every service
# ---------------------------------------------------------------------------

def test_container_name_is_stripped():
    """No service in the generated compose has a container_name key."""
    intake = _make_intake()
    result = _parse(generate_compose(intake, "testslug", base_compose_path=_BASE_COMPOSE))
    for svc_name, svc in result["services"].items():
        assert "container_name" not in svc, (
            f"Service {svc_name!r} still has container_name set"
        )


# ---------------------------------------------------------------------------
# Test 11 — domain substitution
# ---------------------------------------------------------------------------

def test_domain_substitution():
    """${DOMAIN:-kkb} placeholders in volume paths are replaced with the project_slug."""
    intake = _make_intake()
    slug = "myproject"
    yaml_str = generate_compose(intake, slug, base_compose_path=_BASE_COMPOSE)

    # The placeholder must not appear literally in the output
    assert "${DOMAIN:-kkb}" not in yaml_str, "${DOMAIN:-kkb} was not substituted"
    assert "${DOMAIN}" not in yaml_str, "${DOMAIN} was not substituted"

    # The slug must appear in the output (volume mount paths use it)
    assert slug in yaml_str, f"project slug {slug!r} not found in output"


# ---------------------------------------------------------------------------
# Test 12 — missing base template raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_missing_base_template_raises(tmp_path: Path):
    """FileNotFoundError is raised when base_compose_path does not exist."""
    intake = _make_intake()
    missing = tmp_path / "nonexistent.yml"
    with pytest.raises(FileNotFoundError, match=str(missing)):
        generate_compose(intake, "slug", base_compose_path=missing)


# ---------------------------------------------------------------------------
# Test 13 — long-form depends_on dict is also pruned
# ---------------------------------------------------------------------------

def test_long_form_depends_on_pruned(tmp_path: Path):
    """Long-form depends_on dicts also have removed services stripped out."""
    fake_compose = tmp_path / "docker-compose.yml"
    fake_compose.write_text(
        """
services:
  knowledge_engine:
    image: foo
  agent_core:
    image: bar
    depends_on:
      knowledge_engine:
        condition: service_healthy
      redis:
        condition: service_healthy
  redis:
    image: redis:7-alpine
"""
    )
    intake = _make_intake(has_kb=False, selected_channels=["web"])
    result = _parse(generate_compose(intake, "slug", base_compose_path=fake_compose))
    agent_depends = result["services"]["agent_core"].get("depends_on", {})
    assert "knowledge_engine" not in agent_depends
    assert "redis" in agent_depends


# ---------------------------------------------------------------------------
# Test 14 — depends_on becomes absent when all deps are stripped
# ---------------------------------------------------------------------------

def test_depends_on_removed_when_all_deps_stripped(tmp_path: Path):
    """depends_on key is removed entirely when all its entries are stripped."""
    fake_compose = tmp_path / "docker-compose.yml"
    fake_compose.write_text(
        """
services:
  knowledge_engine:
    image: foo
  agent_core:
    image: bar
    depends_on:
      - knowledge_engine
"""
    )
    intake = _make_intake(has_kb=False, selected_channels=["web"])
    result = _parse(generate_compose(intake, "slug", base_compose_path=fake_compose))
    assert "depends_on" not in result["services"].get("agent_core", {})
