from __future__ import annotations

import re
from typing import Any


def _safe_description(record: dict[str, Any]) -> str:
    """Remove exact known distinct examples from a business description.

    Free-text descriptions still require content review; this cannot
    guarantee redaction of examples that do not appear in distinct_values.
    """
    description = str(record.get("description") or "").strip()
    for value in sorted(record.get("distinct_values") or [], key=lambda x: len(str(x)), reverse=True):
        value_text = str(value or "").strip()
        if len(value_text) >= 3:
            description = re.sub(re.escape(value_text), "[example omitted]", description, flags=re.I)
    description = re.sub(r"\bR\d{3,}\b", "[internal code]", description, flags=re.I)
    return description


def resolver_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Identifiers help candidate selection; raw values never enter the resolver."""
    return {
        "rank": record.get("rank"),
        "column_name": record.get("column_name"),
        "table_name": record.get("table_name"),
        "description": _safe_description(record),
        "units": record.get("units") or [],
    }


def explanation_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Small allowlist supplied to the user-facing language model."""
    return {
        "field": record.get("column_name"),
        "business_description": _safe_description(record),
        "units": record.get("units") or [],
    }


def public_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Safe match summary; no table IDs, values, roles or technical types."""
    return {"field": str(record.get("column_name") or "")}
