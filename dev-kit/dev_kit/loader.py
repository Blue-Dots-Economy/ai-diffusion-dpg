"""
dev_kit/loader.py

Loads, merges, and validates DPG service configurations.

For each DPG service, the loader:
  1. Reads dev-kit/dpg/{service}.yaml        — DPG framework defaults
  2. Reads dev-kit/configs/{domain}/{service}.yaml  — domain-specific values
  3. Deep-merges: domain values override DPG defaults on any conflict
  4. Validates the merged result against the Pydantic model in schema.py
  5. Returns a validated, typed config object

Usage as a library:
    from dev_kit.loader import load_agent_core
    config = load_agent_core("kkb")

Usage as a CLI (validate or build merged YAML):
    python -m dev_kit.loader validate --domain kkb
    python -m dev_kit.loader build   --domain kkb --output /tmp/merged/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dev_kit.schema import (
    ActionGatewayConfig,
    AgentCoreConfig,
    KnowledgeEngineConfig,
    ObservabilityLayerConfig,
    MemoryLayerConfig,
    ReachLayerConfig,
    TrustLayerConfig,
)

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# dev_kit/loader.py lives inside the dev_kit package; go up one level to reach
# the dev-kit root (where dpg/ and configs/ directories live).
_KIT_ROOT = Path(__file__).parent.parent


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file. Returns empty dict if the file is empty or absent."""
    if not path.exists():
        return {}
    with path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base.

    Args:
        base: Base dictionary.
        override: Dictionary whose values take precedence.

    Returns:
        Merged dictionary. Lists in override replace lists in base entirely.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_and_merge(domain: str, service: str) -> dict[str, Any]:
    """Load and merge DPG defaults and domain config for a given service.

    Args:
        domain: Domain name, e.g. "kkb".
        service: Service name, e.g. "agent_core".

    Returns:
        Merged raw dict (not yet validated).

    Raises:
        FileNotFoundError: If neither the DPG default nor domain config exists.
    """
    dpg_path = _KIT_ROOT / "dpg" / f"{service}.yaml"
    domain_path = _KIT_ROOT / "configs" / domain / f"{service}.yaml"

    dpg_cfg = _load_yaml(dpg_path)
    domain_cfg = _load_yaml(domain_path)

    if not dpg_cfg and not domain_cfg:
        raise FileNotFoundError(
            f"No config found for service '{service}' "
            f"(checked {dpg_path} and {domain_path})"
        )

    return _deep_merge(dpg_cfg, domain_cfg)


# ---------------------------------------------------------------------------
# Public API — one function per service
# ---------------------------------------------------------------------------

def load_agent_core(domain: str) -> AgentCoreConfig:
    """Load and validate merged Agent Core config for the given domain."""
    merged = _load_and_merge(domain, "agent_core")
    return AgentCoreConfig(**merged)


def load_knowledge_engine(domain: str) -> KnowledgeEngineConfig:
    """Load and validate merged Knowledge Engine config for the given domain."""
    merged = _load_and_merge(domain, "knowledge_engine")
    return KnowledgeEngineConfig(**merged)


def load_trust_layer(domain: str) -> TrustLayerConfig:
    """Load and validate merged Trust Layer config for the given domain."""
    merged = _load_and_merge(domain, "trust_layer")
    return TrustLayerConfig(**merged)


def load_memory_layer(domain: str) -> MemoryLayerConfig:
    """Load and validate merged Memory Layer config for the given domain."""
    merged = _load_and_merge(domain, "memory_layer")
    return MemoryLayerConfig(**merged)


def load_observability_layer(domain: str) -> ObservabilityLayerConfig:
    """Load and validate merged Observability Layer config for the given domain."""
    merged = _load_and_merge(domain, "observability_layer")
    return ObservabilityLayerConfig(**merged)


def load_action_gateway(domain: str) -> ActionGatewayConfig:
    """Load and validate merged Action Gateway config for the given domain."""
    merged = _load_and_merge(domain, "action_gateway")
    return ActionGatewayConfig(**merged)


def load_reach_layer(domain: str) -> ReachLayerConfig:
    """Load and validate merged Reach Layer config for the given domain."""
    merged = _load_and_merge(domain, "reach_layer")
    return ReachLayerConfig(**merged)


# ---------------------------------------------------------------------------
# All-services helpers
# ---------------------------------------------------------------------------

_LOADERS = {
    "agent_core": load_agent_core,
    "knowledge_engine": load_knowledge_engine,
    "trust_layer": load_trust_layer,
    "memory_layer": load_memory_layer,
    "observability_layer": load_observability_layer,
    "action_gateway": load_action_gateway,
    "reach_layer": load_reach_layer,
}


def validate_all(domain: str) -> dict[str, bool]:
    """Validate all 7 service configs for a domain.

    Args:
        domain: Domain name, e.g. "kkb".

    Returns:
        Dict of {service_name: True/False} — True means valid. Prints
        validation errors to stderr.
    """
    results: dict[str, bool] = {}
    for service, loader_fn in _LOADERS.items():
        try:
            loader_fn(domain)
            results[service] = True
            print(f"  \u2713  {service}")
        except (ValidationError, FileNotFoundError, Exception) as exc:
            results[service] = False
            print(f"  \u2717  {service}: {exc}", file=sys.stderr)
    return results


def build_all(domain: str, output_dir: Path) -> None:
    """Merge DPG defaults and domain values for all 7 services and write merged YAML files.

    Args:
        domain: Domain name, e.g. "kkb".
        output_dir: Directory to write merged YAML files into.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for service in _LOADERS:
        merged = _load_and_merge(domain, service)
        out_path = output_dir / f"{service}.yaml"
        with out_path.open("w") as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"  wrote  {out_path}")


# ---------------------------------------------------------------------------
# Schema description helpers (used by conversation agent prompt builders)
# ---------------------------------------------------------------------------

def get_schema_descriptions(block: str) -> dict[str, str]:
    """Extract field descriptions from the Pydantic model for a given block.

    Recursively traverses nested Pydantic models to build a flat dict of
    dot-notation field paths to their descriptions. Used by the conversation
    agent to auto-generate phase-specific prompt context.

    Args:
        block: Block name, e.g. "agent_core".

    Returns:
        Flat dict of {field_path: description}. Empty dict for unknown blocks.
    """
    _map: dict[str, type] = {
        "agent_core": AgentCoreConfig,
        "knowledge_engine": KnowledgeEngineConfig,
        "trust_layer": TrustLayerConfig,
        "memory_layer": MemoryLayerConfig,
        "observability_layer": ObservabilityLayerConfig,
        "action_gateway": ActionGatewayConfig,
        "reach_layer": ReachLayerConfig,
    }
    model_cls = _map.get(block)
    if model_cls is None:
        return {}
    return _extract_field_descriptions(model_cls, prefix="")


def _extract_field_descriptions(model_cls: type, prefix: str) -> dict[str, str]:
    """Recursively extract Field descriptions from a Pydantic model.

    Args:
        model_cls: Pydantic BaseModel subclass to introspect.
        prefix: Dot-notation prefix for nested fields.

    Returns:
        Flat dict of {field_path: description}.
    """
    from pydantic import BaseModel

    result: dict[str, str] = {}
    for field_name, field_info in model_cls.model_fields.items():
        path = f"{prefix}.{field_name}" if prefix else field_name
        if field_info.description:
            result[path] = field_info.description
        annotation = field_info.annotation
        if annotation is not None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            result.update(_extract_field_descriptions(annotation, prefix=path))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    """Entry point for the dev_kit.loader CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m dev_kit.loader",
        description="Validate or build merged DPG configs for a domain.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    val = sub.add_parser("validate", help="Validate all service configs for a domain.")
    val.add_argument("--domain", required=True, help="Domain name (e.g. kkb)")

    # build
    build = sub.add_parser(
        "build",
        help="Merge DPG defaults + domain configs and write merged YAML files.",
    )
    build.add_argument("--domain", required=True, help="Domain name (e.g. kkb)")
    build.add_argument(
        "--output",
        required=True,
        help="Output directory for merged YAML files.",
    )

    args = parser.parse_args()

    if args.command == "validate":
        print(f"Validating configs for domain: {args.domain}")
        results = validate_all(args.domain)
        failed = [s for s, ok in results.items() if not ok]
        if failed:
            print(f"\n{len(failed)} service(s) failed validation.", file=sys.stderr)
            sys.exit(1)
        print("\nAll configs valid.")

    elif args.command == "build":
        output_dir = Path(args.output)
        print(f"Building merged configs for domain: {args.domain} \u2192 {output_dir}")
        build_all(args.domain, output_dir)
        print("\nDone.")


if __name__ == "__main__":
    _cli()
