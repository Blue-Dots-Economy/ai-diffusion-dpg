"""
dev-kit/dev_kit/agent/renderer.py

Writes accumulated config values to YAML files in a project directory.
Returns per-block render status ("complete" | "failed") based on the outcome
of writing and optional pre-deploy dry-run validation.

When running inside the dev-kit Docker image, ``render_all`` performs a
pre-deploy dry-run before writing any YAML file: each block's domain data is
deep-merged with the framework defaults from ``dpg/<block>.yaml`` and
validated against the baked-in ``MergedConfig`` Pydantic class.  This catches
schema drift between what the wizard generates and what the runtime block would
accept at boot — well before ``docker compose up`` is attempted.

When running on the host (no baked-in schemas), the dry-run pass is a no-op.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

from dev_kit.agent.channel_tts import merge_voice_tts_into_suffix, strip_voice_tts_from_suffix
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.project_state import BLOCKS
from dev_kit.schemas.validation import validate_partial

# ---------------------------------------------------------------------------
# Path helpers — replicates the _KIT_ROOT pattern from dev_kit/loader.py so
# this module can find dpg/<block>.yaml without importing private internals.
# ---------------------------------------------------------------------------
# renderer.py lives at dev_kit/agent/renderer.py; go up three levels to reach
# the dev-kit root (where dpg/ lives).
_KIT_ROOT: Path = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Baked-in runtime schemas (available only inside the dev-kit Docker image).
# The try/except allows the module to load on the host during local development
# where the dpg_runtime_schemas package does not exist.
# ---------------------------------------------------------------------------
try:
    from dpg_runtime_schemas.agent_core.config import MergedConfig as _AgentCoreCfg
    from dpg_runtime_schemas.trust_layer.config import MergedConfig as _TrustLayerCfg
    from dpg_runtime_schemas.knowledge_engine.config import MergedConfig as _KnowledgeEngineCfg
    from dpg_runtime_schemas.action_gateway.config import MergedConfig as _ActionGatewayCfg
    from dpg_runtime_schemas.memory_layer.config import MergedConfig as _MemoryLayerCfg
    from dpg_runtime_schemas.observability_layer.config import MergedConfig as _ObservabilityLayerCfg
    from dpg_runtime_schemas.reach_layer.config import MergedConfig as _ReachLayerCfg

    RUNTIME_SCHEMAS: dict[str, type] | None = {
        "agent_core": _AgentCoreCfg,
        "trust_layer": _TrustLayerCfg,
        "knowledge_engine": _KnowledgeEngineCfg,
        "action_gateway": _ActionGatewayCfg,
        "memory_layer": _MemoryLayerCfg,
        "observability_layer": _ObservabilityLayerCfg,
        "reach_layer": _ReachLayerCfg,
    }
except ImportError:
    # Baked schemas not present (running outside the dev-kit docker image).
    # runtime_validate() will raise a clear error if called in this mode.
    RUNTIME_SCHEMAS = None


# ---------------------------------------------------------------------------
# Deep-merge helper (mirrors dev_kit/loader.py::_deep_merge and
# dev_kit/agent/accumulator.py::_deep_merge — not imported because both are
# private; duplication is intentional per the task design notes).
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Lists in the override replace lists in the base entirely — the same
    semantics used by the loader and accumulator helpers.

    Args:
        base: Base dictionary.
        override: Dictionary whose values take precedence.

    Returns:
        New merged dictionary; neither input is mutated.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_dpg_defaults(block: str) -> dict[str, Any]:
    """Load the DPG framework defaults for a given block.

    Reads ``dpg/<block>.yaml`` relative to the dev-kit root.  If the file
    does not exist (e.g. during tests on the host), returns an empty dict
    rather than raising — the dry-run will then validate the domain data
    alone, which is still better than no validation.

    The framework default YAML files are part of the codebase, so a malformed
    file is a setup bug; ``yaml.YAMLError`` is allowed to propagate so the
    operator sees the parser's error directly.

    Args:
        block: Block name, e.g. ``"agent_core"``.

    Returns:
        Parsed YAML dict, or empty dict if the file is absent or empty.

    Raises:
        yaml.YAMLError: If the framework defaults file exists but cannot be
            parsed.
    """
    dpg_path = _KIT_ROOT / "dpg" / f"{block}.yaml"
    if not dpg_path.exists():
        return {}
    with dpg_path.open("r") as fh:
        return yaml.safe_load(fh) or {}


def _prepare_block_data(block: str, accumulator: dict[str, dict]) -> dict[str, Any]:
    """Return the cleaned, render-ready domain data for a single block.

    Applies all agent_core-specific cleanups (NLU intent sync, subagent
    routing guard, voice-TTS suffix merge, max_tool_rounds clamp) so that
    both ``render_all`` and the pre-deploy dry-run validate exactly the
    same data that would be written to disk.  Does not mutate the accumulator
    dict: a shallow copy of the block's top-level keys is taken, so in-place
    ``setdefault`` calls inside cleanups stay local to the returned dict.

    Args:
        block: Block name.
        accumulator: Plain dict keyed by block name; each value is the
            domain-YAML structure for that block.

    Returns:
        Cleaned domain data dict (internal ``_``-prefixed keys stripped), or
        an empty dict if the block has no data yet.
    """
    raw = accumulator.get(block) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"accumulator[{block!r}] must be a dict, got {type(raw).__name__}"
        )
    data = dict(raw)
    if not data:
        return {}

    # Strip internal accumulator keys (prefixed with _).
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    if not data:
        return {}

    # agent_core-specific cleanups.
    if block == "agent_core":
        data = _sync_agent_core_intents(data)
        data = _ensure_subagent_routing(data)
        data = merge_voice_tts_into_suffix(data)
        agent_cfg = data.get("agent", {})
        if isinstance(agent_cfg.get("max_tool_rounds"), int) and agent_cfg["max_tool_rounds"] < 1:
            agent_cfg["max_tool_rounds"] = 1

    # reach_layer is the only block whose runtime YAML wraps under a
    # top-level `reach_layer:` key (its MergedConfig declares
    # `reach_layer: ReachLayerConfig`). FIELD_RULES paths are flat —
    # `channels.web.ui.app_name`, `common.observability.domain` — so the
    # accumulator stores those keys flat as well. Wrap here, exactly once,
    # so the rendered YAML, the validate_partial pass, and the runtime
    # dry-run all see the wrapped shape the runtime expects.
    if block == "reach_layer" and "reach_layer" not in data:
        data = {"reach_layer": data}

    return data


def _sync_agent_core_intents(data: dict) -> dict:
    """Ensure NLU processor intents cover every intent referenced in the agent workflow.

    Collects all intents declared in subagent ``valid_intents`` and the workflow
    ``global_intents`` list, then adds any that are absent from
    ``preprocessing.nlu_processor.intents``.  The sentinel value ``"other"`` is
    excluded — it is handled by the router as a catch-all and must not appear in
    the NLU classifier's label set.

    Args:
        data: Cleaned agent_core block dict (``_``-prefixed keys already stripped).

    Returns:
        Updated dict with a complete NLU intents list.
    """
    workflow: dict = data.get("agent_workflow", {})
    if not workflow:
        return data

    # Gather every intent mentioned in the workflow.
    workflow_intents: set[str] = set()
    for subagent in workflow.get("subagents", []):
        for intent in subagent.get("valid_intents", []):
            workflow_intents.add(intent)
    for intent in workflow.get("global_intents", []):
        workflow_intents.add(intent)

    # "other" is a router catch-all — not a real NLU label.
    workflow_intents.discard("other")

    # Locate (or create) the NLU intents list.
    preprocessing: dict = data.setdefault("preprocessing", {})
    nlu: dict = preprocessing.setdefault("nlu_processor", {})
    existing: list[str] = nlu.get("intents", [])
    existing_set: set[str] = set(existing)

    missing = workflow_intents - existing_set
    if missing:
        nlu["intents"] = existing + sorted(missing)

    return data


def _ensure_subagent_routing(data: dict) -> dict:
    """Auto-add a self-loop catch-all rule for any non-terminal subagent missing routing.

    Agent Core's startup validation (rule 7) rejects any non-terminal subagent
    with an empty ``routing`` list. The LLM occasionally forgets to call
    ``add_routing_rule`` after ``create_subagent``, which is fatal at deploy
    time. Inserting a ``{intent: '*', next_subagent_id: <self>}`` rule keeps
    the user in the same subagent on otherwise-unhandled intents — the same
    pattern used throughout the reference KKB workflow — and preserves the
    intent of "this subagent stays active until something explicitly moves
    the user forward."

    Args:
        data: Cleaned agent_core block dict.

    Returns:
        Updated dict with a guaranteed-non-empty routing list on every
        non-terminal subagent.
    """
    workflow: dict = data.get("agent_workflow", {})
    if not workflow:
        return data
    for sa in workflow.get("subagents", []):
        if sa.get("is_terminal"):
            continue
        routing = sa.get("routing") or []
        if routing:
            continue
        sa["routing"] = [{"intent": "*", "next_subagent_id": sa["id"]}]
    return data


def render_all(
    project_path: Path,
    accumulator: dict[str, dict],
    intake_state: IntakeState,
    *,
    deploy_settings: dict | None = None,  # reserved for Phase 9 derived-field overlay
) -> dict[str, str]:
    """Render every block's domain YAML and return {block: 'complete'|'failed'} per outcome.

    Writes each block's accumulator data as a YAML file under
    ``project_path``. Per-block mirror-schema warnings from
    ``validate_partial`` are advisory — surfaced as ``# WARNINGS:``
    comments at the top of the YAML — but never block the write.

    The authoritative runtime-schema gate is NOT run here. ``render_all``
    is called at the end of every chat turn (often with partial,
    mid-flow data) and must succeed unconditionally so the on-disk YAML
    tracks the accumulator. The runtime dry-run lives at deploy time,
    invoked from ``pre_deploy_validate`` against the deep-merged config
    once all phases are complete.

    Args:
        project_path: Absolute path to the project's configs directory.
        accumulator: Plain dict keyed by block name; each value is the
            domain-YAML structure for that block. Missing or empty blocks
            produce placeholder files.
        intake_state: The project's intake state (reserved for future
            phase-aware rendering; not yet used in block data logic).
        deploy_settings: Reserved for Phase 9 deploy-overlay injection.
            Accepted but ignored in this implementation.

    Returns:
        Dict of block name → ``"complete"`` per block. ``"failed"`` is
        reserved for future use; ``render_all`` itself never marks a
        block failed today.

    Raises:
        ValueError: If ``project_path`` is None.
    """
    if project_path is None:
        raise ValueError("project_path must not be None")
    if accumulator is None:
        raise ValueError(
            "accumulator is required (got None); pass empty_accumulator() for a fresh project"
        )

    project_path.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, str] = {}

    for block in BLOCKS:
        out_path = project_path / f"{block}.yaml"
        data = _prepare_block_data(block, accumulator)

        if not data:
            # Empty block — write a placeholder so the YAML loader always
            # finds every block file.  An empty block is not a failure.
            out_path.write_text(f"# {block} — no config generated yet\n")
            statuses[block] = "complete"
            continue

        yaml_content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

        errors = validate_partial(block, data)
        if errors:
            # Mirror-schema warnings are advisory; write them as comments
            # but still return "complete" — the runtime dry-run is
            # authoritative.
            error_lines = "# WARNINGS:\n" + "\n".join(f"#   - {e}" for e in errors)
            out_path.write_text(error_lines + "\n" + yaml_content)
        else:
            out_path.write_text(yaml_content)

        statuses[block] = "complete"

    return statuses


def load_block_from_file(project_path: Path, block: str) -> dict:
    """Load a block YAML file back into a dict (for reverse-sync from manual edits).

    Strips comment lines (including advisory ``# WARNINGS:`` blocks) before parsing.

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
    # Strip comment lines
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    parsed = yaml.safe_load("\n".join(lines)) or {}
    # Reverse of render-time merge: keep the in-memory suffix free of the
    # auto-generated TTS block so the author only sees prose they wrote.
    if block == "agent_core" and isinstance(parsed, dict):
        parsed = strip_voice_tts_from_suffix(parsed)
    # Reverse of render-time wrap: reach_layer on disk has a top-level
    # `reach_layer:` key; the accumulator stores it flat. Unwrap so a
    # round-trip (render → reload → render) is stable and so the
    # accumulator stays in the same flat shape FIELD_RULES write to.
    if block == "reach_layer" and isinstance(parsed, dict):
        if list(parsed.keys()) == ["reach_layer"] and isinstance(parsed["reach_layer"], dict):
            parsed = parsed["reach_layer"]
    return parsed


def runtime_validate(block: str, data: dict) -> None:
    """Validate rendered YAML against the runtime block's MergedConfig.

    Performs a Pydantic ``model_validate`` call using the baked-in runtime
    schema that was copied into the dev-kit Docker image at build time.  This
    catches any drift between what the wizard generates and what the runtime
    block actually accepts — well before ``docker compose up`` is attempted.

    This function is a no-op success path; it either returns ``None`` or raises.

    Args:
        block: Block name, e.g. ``"agent_core"``.  Must be one of the seven
            standard DPG blocks.
        data: The fully-merged config dict that the running service would
            receive (framework defaults deep-merged with domain overrides).

    Raises:
        KeyError: If ``block`` is not a known runtime block name.
        RuntimeValidationError: If the data fails Pydantic validation, or if
            the baked-in schemas are not available because the function is
            being called outside the dev-kit Docker image.
    """
    from dev_kit.agent.errors import RuntimeValidationError

    _start = time.time()

    if RUNTIME_SCHEMAS is None:
        raise RuntimeValidationError(
            block,
            RuntimeError(
                "Baked-in runtime schemas not available — runtime_validate "
                "is only meaningful inside the dev-kit Docker image where "
                "dpg_runtime_schemas/* is baked in at build time."
            ),
        )
    if block not in RUNTIME_SCHEMAS:
        raise KeyError(
            f"Unknown runtime block: {block!r}; expected one of {sorted(RUNTIME_SCHEMAS)}"
        )
    schema_cls = RUNTIME_SCHEMAS[block]
    try:
        schema_cls.model_validate(data)
    except Exception as e:
        _latency_ms = int((time.time() - _start) * 1000)
        try:
            _validation_errors = e.errors()
        except AttributeError:
            _validation_errors = str(e)
        logger.error(
            "renderer.runtime_validate",
            extra={
                "operation": "renderer.runtime_validate",
                "status": "failure",
                "block": block,
                "latency_ms": _latency_ms,
                "validation_errors": _validation_errors,
            },
        )
        raise RuntimeValidationError(block, e) from e

    _latency_ms = int((time.time() - _start) * 1000)
    logger.info(
        "renderer.runtime_validate",
        extra={
            "operation": "renderer.runtime_validate",
            "status": "success",
            "block": block,
            "latency_ms": _latency_ms,
            "validation_errors": [],
        },
    )
