"""
Shared pytest config for reach_layer/cli tests.

Puts the sibling ``reach_layer/base`` package and the local ``src`` package
on sys.path so imports resolve without installing the package. The
per-deployable pyproject.toml handles the same resolution for installed use
via ``uv.sources``; this conftest mirrors it for bare pytest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CLI_DIR = _HERE.parent
_REACH_LAYER_DIR = _CLI_DIR.parent
_BASE_SRC = _REACH_LAYER_DIR / "base"

# Expose the CLI channel code as top-level ``src`` package.
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))

# Load the shared base package as ``reach_layer_base``. The source files
# live flat inside ``reach_layer/base/``; we register the directory under the
# ``reach_layer_base`` module name so ``from reach_layer_base import ...``
# in channel code resolves correctly.
if "reach_layer_base" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "reach_layer_base",
        _BASE_SRC / "__init__.py",
        submodule_search_locations=[str(_BASE_SRC)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["reach_layer_base"] = module
    spec.loader.exec_module(module)
