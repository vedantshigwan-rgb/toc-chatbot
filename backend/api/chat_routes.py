from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from auth.dependencies import current_user_email, get_current_user

from starlette.concurrency import run_in_threadpool
from agents.query_executor import run_sql_paginated

import csv
import io

from fastapi.responses import StreamingResponse
from agents.query_executor import stream_sql_csv_rows

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




@router.get("/messages/{message_id}/page")
async def get_message_page(
    message_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
):
    user_id = _current_user_id(request)
    store = _chat_store(request)

    # Retrieve the assistant message belonging to this user.
    message = await store.get_message(
        message_id=message_id,
        user_id=user_id,
    )

    if message is None or message.get("role") != "assistant":
        raise HTTPException(
            status_code=404,
            detail="Assistant report message not found.",
        )

    # Only paginate reports whose SQL was actually executed.
    if message.get("query_executed") is not True:
        raise HTTPException(
            status_code=409,
            detail="This message does not have an executed SQL report.",
        )

    sql = message.get("sql")

    if not isinstance(sql, str) or not sql.strip():
        raise HTTPException(
            status_code=409,
            detail="Executed SQL is not available for this message.",
        )

    try:
        page_result = await run_in_threadpool(
            run_sql_paginated,
            sql=sql,
            page=page,
            page_size=50,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve this report page.",
        ) from error

    return {
        "status": "success",
        "message_id": message_id,
        "page": page_result["page"],
        "page_size": page_result["page_size"],
        "total_count": page_result["total_count"],
        "total_pages": page_result["total_pages"],
        "results": jsonable_encoder(page_result["results"]),
    }


@router.get("/messages/{message_id}/export.csv")
async def export_message_csv(
    message_id: str,
    request: Request,
):
    user_id = _current_user_id(request)
    store = _chat_store(request)

    # Retrieve the saved assistant message belonging to this user.
    message = await store.get_message(
        message_id=message_id,
        user_id=user_id,
    )

    if message is None or message.get("role") != "assistant":
        raise HTTPException(
            status_code=404,
            detail="Assistant report message not found.",
        )

    # Export only messages for which SQL was actually executed.
    if message.get("query_executed") is not True:
        raise HTTPException(
            status_code=409,
            detail="This message does not have an executed SQL report.",
        )

    # Use the SQL saved with the assistant message.
    sql = message.get("sql")

    if not isinstance(sql, str) or not sql.strip():
        raise HTTPException(
            status_code=409,
            detail="Executed SQL is not available for this message.",
        )

    # Do not allow a stored non-SELECT statement to be exported.
    if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
        raise HTTPException(
            status_code=400,
            detail="Only SELECT report queries can be exported.",
        )

    # Open the query before starting the download so initial errors
    # can still be returned as HTTP errors.
    row_stream = stream_sql_csv_rows(sql=sql)

    try:
        columns = next(row_stream)
    except StopIteration:
        row_stream.close()
        raise HTTPException(
            status_code=500,
            detail="The report returned no column metadata.",
        )
    except Exception as error:
        row_stream.close()
        raise HTTPException(
            status_code=500,
            detail="Unable to export this report.",
        ) from error

    def generate_csv():
        try:
            # UTF-8 BOM helps Excel recognize UTF-8 CSV files.
            yield "\ufeff"

            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(columns)
            yield buffer.getvalue()
            buffer.close()

            for batch in row_stream:
                for row in batch:
                    buffer = io.StringIO(newline="")
                    writer = csv.writer(buffer)
                    writer.writerow([
                        row.get(column)
                        for column in columns
                    ])
                    yield buffer.getvalue()
                    buffer.close()
        finally:
            row_stream.close()

    safe_message_id = "".join(
        character
        for character in message_id
        if character.isalnum() or character in "-_"
    )

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="report_{safe_message_id}.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )
