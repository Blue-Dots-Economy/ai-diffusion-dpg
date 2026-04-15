"""
Shared pytest config for reach_layer/web tests.

Registers the sibling ``reach_layer/base`` package on sys.path as
``reach_layer_base`` so imports resolve without installing the package,
and exposes the local ``src`` / server modules to the tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Auth env vars must be populated before server.py is imported so the
# module-level FastAPI app singleton can be created even when tests run
# against a domain config that sets auth.enabled=true.
os.environ.setdefault("REACH_SESSION_SECRET", "test-secret-" + "x" * 32)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")

_HERE = Path(__file__).resolve().parent
_WEB_DIR = _HERE.parent
_BASE_SRC = _WEB_DIR.parent / "base"

if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

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
