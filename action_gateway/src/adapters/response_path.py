"""Evaluate a path expression against a decoded JSON response.

Domain-agnostic by construction: this module knows how to walk a structure,
never what any particular key means. Which paths matter is declared in the
domain's connector config, exactly as the existing ``projection`` declares
``role: item_state.role``.

Grammar, deliberately small — three operators, no expression language:

    a.b.c              nested key lookup
    a[k=v]             the FIRST element of list ``a`` whose field ``k`` == ``v``
    a[0]               element by index
    a[k=v][0].f        the operators compose left to right

Comparison in ``[k=v]`` is on the string form of the value, so ``[key=has_age]``
matches whether the upstream sends ``"has_age"`` and ``[active=true]`` matches
whether it sends ``true`` or ``"true"``. That is looser than a typed compare and
deliberately so: a connector path should not have to know how a given backend
spells its booleans.
"""

from __future__ import annotations

import re
from typing import Any

# name, then zero or more [..] selectors
_SEGMENT = re.compile(r"([^.\[\]]+)((?:\[[^\]]*\])*)")
_SELECTOR = re.compile(r"\[([^\]]*)\]")


def _select(value: Any, selector: str) -> Any:
    """Apply one ``[...]`` selector to ``value``.

    Args:
        value: The structure the selector applies to.
        selector: Selector body without brackets — an index, or ``k=v``.

    Returns:
        The selected element, or None when it does not resolve.
    """
    if not isinstance(value, list):
        return None
    if "=" in selector:
        key, _, wanted = selector.partition("=")
        key, wanted = key.strip(), wanted.strip()
        for element in value:
            if isinstance(element, dict) and str(element.get(key)) == wanted:
                return element
        return None
    try:
        return value[int(selector)]
    except (ValueError, IndexError):
        return None


def resolve(data: Any, path: str) -> Any:
    """Resolve a path expression against a decoded response.

    Args:
        data: The decoded JSON response.
        path: A path in the grammar above, e.g. ``items[status=live][0].id``.

    Returns:
        The value at ``path``, or None if any step does not resolve. Never
        raises: a connector path that no longer matches the upstream shape
        must degrade to "absent", not break the tool call.
    """
    if not path:
        return None
    current = data
    for name, selectors in _SEGMENT.findall(path):
        if not isinstance(current, dict):
            return None
        current = current.get(name)
        for selector in _SELECTOR.findall(selectors):
            current = _select(current, selector)
            if current is None:
                return None
        if current is None:
            return None
    return current
