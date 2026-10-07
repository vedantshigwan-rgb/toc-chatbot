from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from auth.dependencies import current_user_email, get_current_user


router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(get_current_user)],
)


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]


def _current_user_id(request: Request) -> str:
    return current_user_email(request)


def _chat_store(request: Request):
    store = getattr(
        request.app.state,
        "chat_store",
        None,
    )

    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Chat storage is unavailable.",
        )

    return store


@router.get("/chats")
async def list_chats(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    store = _chat_store(request)
    user_id = _current_user_id(request)

    chats = await store.list_chats(
        user_id=user_id,
        limit=limit,
    )

    return {
        "status": "success",
        "chats": jsonable_encoder(chats),
    }


@router.get("/chats/{chat_id}/messages")
async def list_chat_messages(
    chat_id: str,
    request: Request,
):
    store = _chat_store(request)
    user_id = _current_user_id(request)

    try:
        messages = await store.list_messages(
            chat_id=chat_id,
            user_id=user_id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    return {
        "status": "success",
        "chat_id": chat_id,
        "messages": jsonable_encoder(messages),
    }


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: str,
    request: Request,
):
    store = _chat_store(request)
    user_id = _current_user_id(request)

    deleted = await store.delete_chat(
        chat_id=chat_id,
        user_id=user_id,
    )

    if deleted is None:
        raise HTTPException(
            status_code=404,
            detail="Chat not found.",
        )

    return {
        "status": "success",
        **deleted,
    }


@router.post("/messages/{message_id}/feedback")
async def submit_message_feedback(
    message_id: str,
    payload: FeedbackRequest,
    request: Request,
):
    store = _chat_store(request)
    user_id = _current_user_id(request)

    try:
        message = await store.set_feedback(
            message_id=message_id,
            user_id=user_id,
            rating=payload.rating,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    if message is None:
        raise HTTPException(
            status_code=404,
            detail="Assistant message not found.",
        )

    flagged_result = None
    flagged_service = getattr(
        request.app.state,
        "flagged_faq_service",
        None,
    )

    if flagged_service is not None:
        try:
            source_user_message = None
            source_user_message_id = str(
                message.get("source_user_message_id") or ""
            ).strip()

            if source_user_message_id:
                source_user_message = await store.get_message(
                    message_id=source_user_message_id,
                    user_id=user_id,
                )

            if source_user_message is None:
                reply_context = await store.get_reply_context(
                    message_id=message_id,
                    user_id=user_id,
                )
                if reply_context:
                    source_user_message = reply_context.get(
                        "source_user_message"
                    )

            flagged_result = await flagged_service.record_rating_candidate(
                rating=payload.rating,
                assistant_message=message,
                source_user_message=source_user_message,
            )
        except Exception as error:
            # Rating storage in Mongo succeeded already. The Excel learning
            # queue is best-effort and must never make thumbs feedback fail.
            flagged_result = {
                "stored": False,
                "error": str(error),
            }

    return {
        "status": "success",
        "message_id": message_id,
        "feedback": payload.rating,
        "flagged_faq": flagged_result,
    }
