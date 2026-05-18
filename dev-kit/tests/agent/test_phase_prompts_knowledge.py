"""Tests for dev_kit.agent.phase_prompts.knowledge."""
from __future__ import annotations


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False, is_multi_turn=False,
        needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="en", supported_languages=["en"],
        domain_description="test", project_name="test_project",
    )
    base.update(overrides)
    from dev_kit.agent.intake_state import IntakeState
    return IntakeState(**base)


def _fake_field(path: str, description: str = "A field"):
    from dev_kit.agent.field_rules import FieldRule
    rule = FieldRule(category="chat", phase="knowledge", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.knowledge import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Knowledge base" in result


def test_build_contains_field_section():
    result = build([], "", "", _intake())
    assert "## Fields to capture this phase" in result


def test_build_contains_pydantic_schema_section():
    result = build([], "", "", _intake())
    assert "## Pydantic schemas" in result


def test_build_injects_pydantic_schemas_param():
    result = build([], "class FooSection(BaseModel): pass", "", _intake())
    assert "class FooSection(BaseModel): pass" in result


def test_build_injects_cross_phase_refs_param():
    result = build([], "", "preset_value=xyz", _intake())
    assert "preset_value=xyz" in result


def test_build_renders_pending_fields():
    fields = [
        _fake_field(
            "knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name",
            "ChromaDB collection name",
        ),
        _fake_field(
            "knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters",
            "Intent-to-doc_type mapping",
        ),
    ]
    result = build(fields, "", "", _intake())
    assert "knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name" in result
    assert "ChromaDB collection name" in result
    assert "knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters" in result
    assert "Intent-to-doc_type mapping" in result


def test_knowledge_references_project_name():
    """Collection name default must embed the project name."""
    result = build([], "", "", _intake(project_name="foo"))
    assert "foo_kb" in result


def test_knowledge_prompt_asks_azure_blob_question() -> None:
    """The knowledge phase must ask the operator whether KB docs live in
    Azure Blob Storage and call `update_intake` with the answer.

    Without this flag the deploy form has no way to know whether to
    surface AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY / AZURE_CONTAINER_NAME
    inputs. Mirrors the legacy `declare_azure_storage` tool flow on main —
    chat captures the boolean intent; the deploy step collects the actual
    credentials.
    """
    result = build([], "", "", _intake(has_kb=True))
    # The question is asked verbatim.
    assert "Azure Blob Storage" in result
    # The wizard tool that records the answer is mentioned both for yes
    # and no, so the LLM commits the boolean either way.
    assert 'update_intake(field="uses_azure_blob", value=true)' in result
    assert 'update_intake(field="uses_azure_blob", value=false)' in result
    # And the prompt still explicitly forbids asking for credentials in
    # chat — those are deploy-form only.
    assert "NEVER ask for the credentials in chat" in result


def test_knowledge_has_kb_true_note():
    """When the user wants a KB, the prompt instructs the LLM to proceed."""
    result = build([], "", "", _intake(has_kb=True))
    assert "knowledge base" in result.lower()
    assert "required" in result.lower() or "already confirmed" in result.lower()
    # The literal `has_kb=true` leak (the regression we are guarding against)
    # must not appear in user-facing guidance text.
    assert "has_kb=true" not in result
    assert "has_kb=false" not in result


def test_knowledge_has_kb_false_note():
    """When the user did not flag a KB, the prompt instructs the LLM to
    confirm briefly without leaking `has_kb=...` into prose.
    """
    result = build([], "", "", _intake(has_kb=False))
    assert "knowledge base" in result.lower()
    assert "did not flag" in result.lower() or "no kb is needed" in result.lower()
    assert "has_kb=true" not in result
    assert "has_kb=false" not in result
