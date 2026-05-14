"""Tests for phases_config.PHASES declarative phase definitions.

Covers: all 11 phases exist with correct ids and order, PhaseDefinition field
shape, next_default chain, is_relevant predicates, prompt_module naming
convention, and cross-check with router.PHASE_ORDER.

See design §6: docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

import pytest

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.phases_config import PHASES, PhaseDefinition

# Transitional pin: once Task 6.3 wires phase_driver to PHASES and removes the
# duplicate PHASE_ORDER from router.py, delete this import and the
# TestOrderMatchesRouterPhaseOrder class below.
from dev_kit.agent.router import PHASE_ORDER

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CANONICAL_ORDER = (
    "tier", "language", "knowledge", "memory", "user_state",
    "trust", "tools", "workflow", "observability", "reach", "review",
)


def _intake(**overrides) -> IntakeState:
    """Build a minimal IntakeState, override any field via kwargs."""
    base = dict(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="",
        project_name="p",
    )
    base.update(overrides)
    return IntakeState(**base)


# ---------------------------------------------------------------------------
# Test 1: 11 phases exist with expected ids and order
# ---------------------------------------------------------------------------

class TestPhasesExistAndOrder:
    """All 11 phases must exist and appear in the canonical order."""

    def test_phase_count(self):
        assert len(PHASES) == 11

    def test_phase_keys_in_canonical_order(self):
        assert list(PHASES.keys()) == list(CANONICAL_ORDER)

    def test_every_phase_id_matches_key(self):
        for key, defn in PHASES.items():
            assert defn.id == key, f"Phase key {key!r} has mismatched id {defn.id!r}"


# ---------------------------------------------------------------------------
# Test 2: PhaseDefinition field shape — frozen dataclass, all fields present
# ---------------------------------------------------------------------------

class TestPhaseDefinitionShape:
    """PhaseDefinition must be a frozen dataclass with the correct fields."""

    def test_is_frozen(self):
        defn = PHASES["tier"]
        with pytest.raises((AttributeError, TypeError)):
            defn.id = "mutated"  # type: ignore[misc]

    def test_has_id(self):
        for defn in PHASES.values():
            assert isinstance(defn.id, str) and defn.id

    def test_has_label(self):
        for defn in PHASES.values():
            assert isinstance(defn.label, str) and defn.label

    def test_has_prompt_module(self):
        for defn in PHASES.values():
            assert isinstance(defn.prompt_module, str) and defn.prompt_module

    def test_has_next_default(self):
        # next_default is Optional[str] — only review has None.
        for key, defn in PHASES.items():
            if key == "review":
                assert defn.next_default is None
            else:
                assert isinstance(defn.next_default, str) and defn.next_default

    def test_has_is_relevant_callable_or_none(self):
        for defn in PHASES.values():
            assert defn.is_relevant is None or callable(defn.is_relevant)

    def test_is_phase_definition_instances(self):
        for defn in PHASES.values():
            assert isinstance(defn, PhaseDefinition)


# ---------------------------------------------------------------------------
# Test 3: next_default chain forms a proper linked list ending at None
# ---------------------------------------------------------------------------

class TestNextDefaultChain:
    """next_default must form a proper singly-linked list through all 11 phases."""

    def test_chain_starts_at_tier(self):
        assert PHASES["tier"].next_default == "language"

    def test_full_chain_matches_canonical_order(self):
        """Walk next_default from 'tier' and verify it visits all phases in order."""
        visited = []
        current: str | None = "tier"
        while current is not None:
            visited.append(current)
            current = PHASES[current].next_default
        assert visited == list(CANONICAL_ORDER)

    def test_each_next_default_points_to_successor(self):
        for i, phase in enumerate(CANONICAL_ORDER[:-1]):
            expected_next = CANONICAL_ORDER[i + 1]
            assert PHASES[phase].next_default == expected_next, (
                f"PHASES[{phase!r}].next_default should be {expected_next!r}, "
                f"got {PHASES[phase].next_default!r}"
            )

    def test_review_next_default_is_none(self):
        assert PHASES["review"].next_default is None

    def test_chain_terminates(self):
        """Walk chain and confirm it terminates (no cycles, ends exactly at None)."""
        visited = set()
        current: str | None = "tier"
        while current is not None:
            assert current not in visited, f"Cycle detected at {current!r}"
            visited.add(current)
            current = PHASES[current].next_default
        assert len(visited) == 11


# ---------------------------------------------------------------------------
# Test 4: is_relevant predicates
# ---------------------------------------------------------------------------

class TestIsRelevantPredicates:
    """Verify gated phases flip on the correct flag; always-relevant phases stay True."""

    # --- ungated phases (must always return True) ---

    def test_tier_always_relevant(self):
        for flags in [_intake(), _intake(has_kb=True, has_external_tools=True, is_companion_style=True)]:
            assert PHASES["tier"].is_relevant(flags) is True

    def test_language_always_relevant(self):
        for flags in [_intake(), _intake(has_kb=True)]:
            assert PHASES["language"].is_relevant(flags) is True

    def test_memory_always_relevant(self):
        """memory phase should always be relevant (deviation from spec's lambda)."""
        assert PHASES["memory"].is_relevant(_intake()) is True
        assert PHASES["memory"].is_relevant(_intake(is_multi_turn=False, needs_persistent_user_data=False)) is True
        assert PHASES["memory"].is_relevant(_intake(is_multi_turn=True)) is True

    def test_trust_always_relevant(self):
        for flags in [_intake(), _intake(has_kb=True)]:
            assert PHASES["trust"].is_relevant(flags) is True

    def test_workflow_always_relevant(self):
        assert PHASES["workflow"].is_relevant(_intake()) is True

    def test_observability_always_relevant(self):
        assert PHASES["observability"].is_relevant(_intake()) is True

    def test_reach_always_relevant(self):
        assert PHASES["reach"].is_relevant(_intake()) is True

    def test_review_always_relevant(self):
        assert PHASES["review"].is_relevant(_intake()) is True

    # --- gated: knowledge (has_kb) ---

    def test_knowledge_relevant_when_has_kb_true(self):
        assert PHASES["knowledge"].is_relevant(_intake(has_kb=True)) is True

    def test_knowledge_irrelevant_when_has_kb_false(self):
        assert PHASES["knowledge"].is_relevant(_intake(has_kb=False)) is False

    def test_knowledge_flag_toggles(self):
        off = _intake(has_kb=False)
        on = _intake(has_kb=True)
        assert PHASES["knowledge"].is_relevant(off) is False
        assert PHASES["knowledge"].is_relevant(on) is True

    # --- gated: user_state (is_companion_style) ---

    def test_user_state_relevant_when_companion_true(self):
        assert PHASES["user_state"].is_relevant(_intake(is_companion_style=True)) is True

    def test_user_state_irrelevant_when_companion_false(self):
        assert PHASES["user_state"].is_relevant(_intake(is_companion_style=False)) is False

    def test_user_state_flag_toggles(self):
        off = _intake(is_companion_style=False)
        on = _intake(is_companion_style=True)
        assert PHASES["user_state"].is_relevant(off) is False
        assert PHASES["user_state"].is_relevant(on) is True

    # --- gated: tools (has_external_tools) ---

    def test_tools_relevant_when_has_external_tools_true(self):
        assert PHASES["tools"].is_relevant(_intake(has_external_tools=True)) is True

    def test_tools_irrelevant_when_has_external_tools_false(self):
        assert PHASES["tools"].is_relevant(_intake(has_external_tools=False)) is False

    def test_tools_flag_toggles(self):
        off = _intake(has_external_tools=False)
        on = _intake(has_external_tools=True)
        assert PHASES["tools"].is_relevant(off) is False
        assert PHASES["tools"].is_relevant(on) is True


# ---------------------------------------------------------------------------
# Test 5: prompt_module values are non-empty strings matching the phase id
# ---------------------------------------------------------------------------

class TestPromptModuleNamingConvention:
    """prompt_module must be a non-empty string that equals the phase id."""

    def test_prompt_module_equals_phase_id(self):
        for key, defn in PHASES.items():
            assert defn.prompt_module == key, (
                f"PHASES[{key!r}].prompt_module should be {key!r}, "
                f"got {defn.prompt_module!r}"
            )

    def test_prompt_module_is_non_empty_string(self):
        for defn in PHASES.values():
            assert isinstance(defn.prompt_module, str)
            assert len(defn.prompt_module) > 0


# ---------------------------------------------------------------------------
# Test 6: Order matches router.PHASE_ORDER (cross-module pin)
# ---------------------------------------------------------------------------

class TestOrderMatchesRouterPhaseOrder:
    """phases_config.PHASES key order must equal router.PHASE_ORDER exactly."""

    def test_phases_keys_equal_router_phase_order(self):
        assert tuple(PHASES.keys()) == PHASE_ORDER, (
            f"PHASES key order {tuple(PHASES.keys())} "
            f"does not match router.PHASE_ORDER {PHASE_ORDER}"
        )

    def test_phase_order_length_matches(self):
        assert len(PHASES) == len(PHASE_ORDER)
