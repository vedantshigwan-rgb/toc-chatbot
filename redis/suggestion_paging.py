"""Stable pagination over an already sorted list of Redis autocomplete values."""
from __future__ import annotations
from typing import Any


def slice_page(values: list[str], *, limit: int, offset: int) -> dict[str, Any]:
    if limit < 1 or offset < 0:
        raise ValueError("Invalid autocomplete pagination")
    selected = values[offset:offset + limit]
    more = offset + len(selected) < len(values)
    return {
        "values": selected,
        "count": len(selected),
        "total_matches": len(values),
        "has_more": more,
        "next_offset": offset + len(selected) if more else None,
    }
