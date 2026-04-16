"""Adapter package for the Action Gateway block.

Exports the ToolAdapter ABC and all concrete adapter implementations so that
the ToolRegistry can import from a single location.
"""
from src.adapters.base import ToolAdapter
from src.adapters.mcp import McpAdapter
from src.adapters.rest_api import RestApiAdapter

__all__ = ["ToolAdapter", "McpAdapter", "RestApiAdapter"]
