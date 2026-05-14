"""Dotted-path resolver with `[key=value]` list-of-objects syntax.

Used by FIELD_RULES to address fields in the nested accumulator dict.
See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.1
and §5 "Path syntax (including list-of-objects)".
"""
from __future__ import annotations

import re
from typing import Any

# Matches a single segment that may include a [key=value] selector.
# Examples: "internal[name=knowledge_retrieval]", "subagents[id=enquiry]", "agent".
_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([^=]+)=([^\]]+)\])?$")


def _parse_segment(segment: str) -> tuple[str, str | None, str | None]:
    """Return (attribute_name, selector_key, selector_value) for a path segment.

    Args:
        segment: A single dot-separated path component, e.g. ``"agent"`` or
            ``"internal[name=knowledge_retrieval]"``.

    Returns:
        A 3-tuple ``(attr, key, value)`` where ``key`` and ``value`` are ``None``
        for plain segments and non-``None`` for list-of-objects selectors.

    Raises:
        ValueError: If the segment does not match the expected pattern.
    """
    m = _SEGMENT_RE.match(segment)
    if not m:
        raise ValueError(f"Invalid path segment: {segment!r}")
    attr, key, value = m.groups()
    return attr, key, value


def _walk_segments(path: str) -> list[tuple[str, str | None, str | None]]:
    """Split a dotted path into a list of parsed segments.

    Args:
        path: A dotted path string such as ``"agent.timeout_ms"`` or
            ``"connectors.internal[name=knowledge_retrieval].route"``.

    Returns:
        Ordered list of ``(attr, selector_key, selector_value)`` tuples.
    """
    return [_parse_segment(seg) for seg in path.split(".")]


def get_path(data: dict, path: str) -> Any:
    """Read the value at ``path`` in ``data``.

    Traverses nested dicts and list-of-objects using ``[key=value]`` selectors.
    Returns ``None`` if any intermediate segment is absent or a list match fails.

    Args:
        data: The nested accumulator dict to read from.
        path: Dotted path, e.g. ``"agent.timeout_ms"`` or
            ``"connectors.internal[name=knowledge_retrieval].route"``.

    Returns:
        The value at the resolved path, or ``None`` if any segment is missing.
    """
    current: Any = data
    for attr, key, value in _walk_segments(path):
        if not isinstance(current, dict):
            return None
        current = current.get(attr)
        if current is None:
            return None
        if key is not None:
            # current should be a list of dicts; find element matching key=value.
            if not isinstance(current, list):
                return None
            matched = next(
                (item for item in current if isinstance(item, dict) and item.get(key) == value),
                None,
            )
            current = matched
            if current is None:
                return None
    return current


def set_path(data: dict, path: str, value: Any) -> None:
    """Write ``value`` at ``path`` in ``data``, creating intermediate nodes as needed.

    For plain segments, intermediate dicts are created on demand. For
    list-of-objects segments (``attr[key=val]``), find-or-append semantics
    apply: the matching element is updated in place, or a new element
    ``{key: val}`` is appended to the list.

    Args:
        data: The nested accumulator dict to write into (mutated in place).
        path: Dotted path, e.g. ``"agent.timeout_ms"`` or
            ``"connectors.internal[name=knowledge_retrieval].route"``.
        value: The value to write at the resolved path.

    Raises:
        ValueError: If a list-of-objects segment is the final segment and
            ``value`` is not a dict.
    """
    segments = _walk_segments(path)
    current: Any = data
    for i, (attr, key, sel_value) in enumerate(segments):
        is_last = i == len(segments) - 1
        if key is None:
            if is_last:
                current[attr] = value
                return
            if attr not in current or not isinstance(current[attr], dict):
                current[attr] = {}
            current = current[attr]
        else:
            # List-of-objects segment.
            if attr not in current or not isinstance(current[attr], list):
                current[attr] = []
            lst = current[attr]
            matched = next(
                (item for item in lst if isinstance(item, dict) and item.get(key) == sel_value),
                None,
            )
            if matched is None:
                matched = {key: sel_value}
                lst.append(matched)
            if is_last:
                # Setting the whole element to a value isn't a sensible operation
                # for list-of-objects; we update the matched dict instead.
                if isinstance(value, dict):
                    matched.update(value)
                else:
                    raise ValueError(
                        f"Cannot set list-of-objects element {attr}[{key}={sel_value}] "
                        f"to non-dict value {value!r}"
                    )
                return
            current = matched


def clear_path(data: dict, path: str) -> None:
    """Remove the value at ``path`` from ``data``. No-op if the path is absent.

    For list-of-objects segments at the end of the path, removes the element
    whose ``key`` matches ``value`` from the list. For plain segments, deletes
    the key from the parent dict.

    Args:
        data: The nested accumulator dict to mutate in place.
        path: Dotted path, e.g. ``"agent.timeout_ms"`` or
            ``"connectors.internal[name=knowledge_retrieval]"``.
    """
    segments = _walk_segments(path)
    current: Any = data
    for i, (attr, key, sel_value) in enumerate(segments):
        is_last = i == len(segments) - 1
        if key is None:
            if is_last:
                if isinstance(current, dict) and attr in current:
                    del current[attr]
                return
            if not isinstance(current, dict) or attr not in current:
                return
            current = current[attr]
        else:
            if not isinstance(current, dict) or attr not in current:
                return
            lst = current[attr]
            if not isinstance(lst, list):
                return
            if is_last:
                current[attr] = [
                    item for item in lst
                    if not (isinstance(item, dict) and item.get(key) == sel_value)
                ]
                return
            matched = next(
                (item for item in lst if isinstance(item, dict) and item.get(key) == sel_value),
                None,
            )
            if matched is None:
                return
            current = matched


__all__ = ["get_path", "set_path", "clear_path"]
