"""Per-phase prompt builders for the deterministic wizard.

Each sub-module exports a single ``build(pending_fields, pydantic_schemas,
cross_phase_refs, intake_state) -> str`` function that returns the
phase-specific addition to the base system prompt.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
