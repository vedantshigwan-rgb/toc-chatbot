from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from auth.dependencies import get_current_user
from intent.classifier import IntentClassifier, IntentClassifierError


router = APIRouter(
    prefix="/intent",
    tags=["intent"],
    dependencies=[Depends(get_current_user)],
)


class IntentRequest(BaseModel):
    input: str = Field(..., min_length=1)
    report_name: Optional[str] = None

    @field_validator("input")
    @classmethod
    def clean_input(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Input cannot be empty.")
        return cleaned

    @field_validator("report_name")
    @classmethod
    def clean_report(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None


@router.post("/classify")
async def classify_intent(payload: IntentRequest, request: Request):
    started_at = time.perf_counter()
    classifier = getattr(request.app.state, "intent_classifier", None)
    if classifier is None:
        classifier = IntentClassifier(pool=request.app.state.pool)

    try:
        result = await classifier.classify(
            user_input=payload.input,
            memory=None,
            quoted_message=None,
            selected_data_context=payload.report_name,
            result_context_available=False,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(
                {
                    "status": "success",
                    "intent": result.intent,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "context_used": False,
                    "report_name": payload.report_name,
                    "latency_ms": latency_ms,
                }
            ),
        )
    except IntentClassifierError as error:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return JSONResponse(
            status_code=502,
            content={
                "status": "failed",
                "error": {
                    "category": "intent_classification_error",
                    "code": error.code,
                    "message": "The message could not be classified at this time.",
                    "retryable": True,
                },
                "latency_ms": latency_ms,
            },
        )
