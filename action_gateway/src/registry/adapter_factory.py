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

from src.adapters.mcp import McpAdapter
from src.adapters.rest_api import RestApiAdapter
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)

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

            adapter_class = ADAPTER_TYPES.get(adapter_type)  # type: ignore[arg-type]
            if adapter_class is None:
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
