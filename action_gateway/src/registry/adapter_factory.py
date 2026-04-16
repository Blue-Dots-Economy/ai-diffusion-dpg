"""AdapterFactory for the Action Gateway block.

This module provides AdapterFactory, which reads a config dict and assembles a
fully-populated AdapterRegistry. It maps the string type keys defined in the
domain YAML to their concrete ToolAdapter classes, instantiates each adapter,
and registers its tool names. MCP adapters are initialised asynchronously so
that tool discovery completes before the server starts accepting requests.
"""
from __future__ import annotations

import logging
import time

from opentelemetry import trace as otel_trace

from src.adapters.mcp import McpAdapter
from src.adapters.rest_api import RestApiAdapter
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)


def _get_tracer() -> otel_trace.Tracer:
    """Return the OTel tracer for AdapterFactory.

    Resolved lazily so tests can install a TracerProvider before the first call.

    Returns:
        opentelemetry.trace.Tracer for this instrumentation scope.
    """
    return otel_trace.get_tracer(__name__)


# Maps the YAML "type" field to the concrete adapter class.
ADAPTER_TYPES: dict[str, type] = {
    "rest_api": RestApiAdapter,
    "mcp": McpAdapter,
}


class AdapterFactory:
    """Builds an AdapterRegistry from a parsed config dict.

    This class contains only a single static method. It is not meant to be
    instantiated.
    """

    @staticmethod
    async def build_registry(config: dict) -> AdapterRegistry:
        """Build an AdapterRegistry from the provided config dict.

        Iterates over config["tools"], instantiates the appropriate adapter
        class for each entry, initialises MCP adapters asynchronously, and
        registers all tool names the adapter exposes. If any adapter fails
        (unknown type, missing env var, initialisation error), that adapter
        is skipped with a logged error and the remaining adapters continue
        to load.

        Args:
            config: Top-level config dict. Must contain a "tools" key whose
                value is a list of adapter config dicts. If the key is missing
                the returned registry will be empty.

        Returns:
            A fully-populated AdapterRegistry ready for use by the server.
        """
        registry = AdapterRegistry()
        tools_config: list[dict] = config.get("tools", [])

        if not isinstance(tools_config, list):
            logger.warning(
                "adapter_factory_invalid_tools_key",
                extra={
                    "operation": "AdapterFactory.build_registry",
                    "status": "skipped",
                    "error": "tools key is not a list",
                },
            )
            return registry

        for tool_config in tools_config:
            adapter_id = tool_config.get("id", "<unknown>")
            adapter_type = tool_config.get("type")
            start = time.time()

            with _get_tracer().start_as_current_span("action.startup.adapter_init") as span:
                span.set_attribute("adapter_type", adapter_type or "unknown")
                span.set_attribute("tool_id", adapter_id)

                adapter_class = ADAPTER_TYPES.get(adapter_type)  # type: ignore[arg-type]
                if adapter_class is None:
                    span.set_attribute("success", False)
                    span.record_exception(ValueError(f"unknown adapter type '{adapter_type}'"))
                    logger.error(
                        "adapter_factory_unknown_type",
                        extra={
                            "operation": "AdapterFactory.build_registry",
                            "status": "failure",
                            "error": f"unknown adapter type '{adapter_type}'",
                            "adapter_id": adapter_id,
                        },
                    )
                    continue

                try:
                    adapter = adapter_class(tool_config)

                    if hasattr(adapter, "initialize") and callable(adapter.initialize):
                        await adapter.initialize()

                    definitions = adapter.get_tool_definitions()
                    for definition in definitions:
                        registry.register(definition.name, adapter)

                    span.set_attribute("success", True)
                    latency_ms = int((time.time() - start) * 1000)
                    logger.info(
                        "adapter_factory_registered",
                        extra={
                            "operation": "AdapterFactory.build_registry",
                            "status": "success",
                            "adapter_id": adapter_id,
                            "adapter_type": adapter_type,
                            "tools_registered": len(definitions),
                            "latency_ms": latency_ms,
                        },
                    )

                except Exception as exc:  # noqa: BLE001
                    span.set_attribute("success", False)
                    span.record_exception(exc)
                    latency_ms = int((time.time() - start) * 1000)
                    logger.error(
                        "adapter_factory_build_error",
                        extra={
                            "operation": "AdapterFactory.build_registry",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "adapter_id": adapter_id,
                            "latency_ms": latency_ms,
                        },
                    )
                    continue

        return registry
