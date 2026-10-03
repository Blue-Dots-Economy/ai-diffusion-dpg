"""
action_gateway/src/config/env_expand.py

${VAR} / ${VAR:-default} expansion for Action Gateway YAML, the same contract
as reach_layer/base/config_loader.py: a set variable wins, an unset one takes
its default, and an unset one without a default is left as written. Only
string scalars are touched. Belongs to the Action Gateway DPG block.
"""
from __future__ import annotations

import os
import re
from typing import Any

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


class UnresolvedEnvPlaceholderError(ValueError):
    """A tool URL still holds a ${VAR} placeholder after expansion."""


def expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` in string scalars.

    Args:
        obj: Parsed YAML (dict, list, scalar).

    Returns:
        The same structure with placeholders expanded.
    """
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            value = os.environ.get(m.group(1))
            if value is not None:
                return value
            return m.group(2) if m.group(2) is not None else m.group(0)
        return _ENV_VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(i) for i in obj]
    return obj


def _static_param_values(params: list | None) -> list[str]:
    return [p.get("value") for p in params or []
            if p.get("source") == "static" and isinstance(p.get("value"), str)]


def check_no_unresolved_urls(config: dict) -> None:
    """Fail startup when a tool's base_url or static param still holds ``${VAR}``.

    Static params are checked at tool level and inside each endpoint.

    Args:
        config: Merged, expanded Action Gateway config.

    Raises:
        UnresolvedEnvPlaceholderError: Naming the tool and the variable.
    """
    for tool in config.get("tools") or []:
        values = [tool.get("base_url") or ""]
        values += _static_param_values(tool.get("params"))
        for endpoint in tool.get("endpoints") or []:
            values += _static_param_values(endpoint.get("params"))
        for value in values:
            m = _ENV_VAR_PATTERN.search(value)
            if m:
                raise UnresolvedEnvPlaceholderError(
                    f"tool '{tool.get('id')}' has an unresolved ${{{m.group(1)}}}: set {m.group(1)} "
                    f"or give the placeholder a default (${{{m.group(1)}:-...}})")
