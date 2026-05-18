"""Tests for AGGREGATED_FIELD_RULES — union of all per-block FIELD_RULES."""
from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES


def test_aggregate_contains_all_blocks():
    blocks = {p.split(".", 1)[0] for p in AGGREGATED_FIELD_RULES.keys()}
    expected = {
        "agent_core", "trust_layer", "knowledge_engine",
        "action_gateway", "memory_layer", "observability_layer", "reach_layer",
    }
    assert blocks == expected


def test_no_duplicate_paths():
    paths = list(AGGREGATED_FIELD_RULES.keys())
    assert len(paths) == len(set(paths))


def test_predetermined_rules_reference_intake_fields_only():
    """Every predetermined rule should reference only IntakeState attribute names."""
    from dev_kit.agent.intake_state import IntakeState
    intake_fields = {f for f in IntakeState.__dataclass_fields__}
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "predetermined" or not rule.rule:
            continue
        # Permissive check: rule is a Python expression. We extract identifiers
        # and check that any whose first char is alpha and which is not a Python
        # keyword/builtin is in intake_fields. Full AST parsing is overkill for
        # this guard; the test catches typos like `has_db` (typo of `has_kb`).
        import re
        idents = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", rule.rule))
        # Also exclude: Python builtins/keywords, known string literal values
        # used in rule expressions (e.g. 'voice' in '"voice" in selected_channels'),
        # f-string prefix 'f', module-level constant names, and kwarg names.
        _KNOWN_LITERALS = {
            # Channel names used as string literals in rule expressions
            "voice", "web",
            # Storage mode literals
            "saved", "anonymous",
            # Route / tool name literals
            "knowledge_engine", "knowledge_retrieval",
            # Python function/expression helpers used in rule expressions
            "slug", "project_slug", "lang_code", "f",
            # Common string literals / kwarg names in rule expressions
            "disabled", "name", "route", "type", "query", "string",
            "workflow", "user_id",
            # JSON-Schema literals used by the knowledge_retrieval
            # connector's input_schema predetermined rule.
            "object", "properties", "required",
        }
        # CamelCase class names and __dunder__ identifiers can't match the
        # lowercase-leading regex above, so they don't need allowlisting.
        # Strip f-string fragment identifiers (e.g. '_knowledge' from f"{slug}_knowledge")
        idents = {i for i in idents if not i.startswith("_")}
        suspect = idents - intake_fields - {"set", "if", "else", "and", "or",
                                             "not", "in", "True", "False", "None"} - _KNOWN_LITERALS
        assert not suspect, f"{path}: unknown identifiers {suspect}"
