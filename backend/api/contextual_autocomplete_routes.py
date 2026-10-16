"""Internal, authenticated Oracle-only API for the Redis autocomplete service."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import get_current_user
from services.contextual_autocomplete_service import (
    ContextualAutocompleteError, query_oracle_values,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/autocomplete", tags=["internal-autocomplete"])


class ContextualRequest(BaseModel):
    report_name: str = Field(min_length=1, max_length=120)
    target_variable: str = Field(min_length=1, max_length=100)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    search_text: str = Field(default="", max_length=100)
    limit: int = Field(default=40, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)


@router.post("/oracle", include_in_schema=False)
def internal_oracle_lookup(
    payload: ContextualRequest,
    user: dict[str, Any] = Depends(get_current_user),
    x_toc_internal_key: str | None = Header(default=None),
):
    # Require BOTH a valid login token and a private service-to-service secret.
    secret = os.getenv("TOC_AUTOCOMPLETE_INTERNAL_KEY", "").strip()
    if not secret or not x_toc_internal_key or not hmac.compare_digest(secret, x_toc_internal_key):
        raise HTTPException(status_code=403, detail="Access denied.")
    try:
        return query_oracle_values(
            report=payload.report_name,
            target=payload.target_variable,
            filters=payload.filters,
            search=payload.search_text,
            limit=payload.limit,
            offset=payload.offset,
        )
    except ContextualAutocompleteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Internal contextual Oracle lookup failed")
        raise HTTPException(status_code=503, detail="Autocomplete lookup is unavailable.") from exc
