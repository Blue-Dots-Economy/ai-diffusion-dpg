"""
Shared pytest config for reach_layer/base tests.

Registers reach_layer/base as the ``reach_layer_base`` module so that
``from reach_layer_base import ...`` resolves correctly when running tests
directly from this directory without installing the package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BASE_SRC = _HERE.parent

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
