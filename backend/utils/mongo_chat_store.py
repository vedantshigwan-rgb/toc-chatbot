from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pymongo import AsyncMongoClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_chat_title(text: str, max_length: int = 60) -> str:
    cleaned = " ".join(str(text or "").split()).strip()

    if not cleaned:
        return "New chat"

    if len(cleaned) <= max_length:
        return cleaned

    return cleaned[: max_length - 1].rstrip() + "…"


def clean_report_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def same_report_name(first: Optional[str], second: Optional[str]) -> bool:
    first_clean = clean_report_name(first)
    second_clean = clean_report_name(second)
    if first_clean is None or second_clean is None:
        return first_clean == second_clean
    return first_clean.casefold() == second_clean.casefold()


class MongoChatStore:
    """
    Persistent chat/message storage.

    This store is deliberately separate from the old workflow store.
    The workflow store tracks workflow execution. This store tracks
    user-visible conversations, replies and feedback.
    """

    def __init__(
        self,
        *,
        mongo_uri: str,
        database_name: str = "toc",
        chats_collection_name: str = "chats",
        messages_collection_name: str = "messages",
    ) -> None:
        self.client = AsyncMongoClient(mongo_uri)
        self.database = self.client[database_name]
        self.chats = self.database[chats_collection_name]
        self.messages = self.database[messages_collection_name]

    async def initialize(self) -> None:
        await self.client.admin.command("ping")

        await self.chats.create_index(
            "chat_id",
            unique=True,
        )

        await self.chats.create_index(
            [
                ("user_id", 1),
                ("updated_at", -1),
            ]
        )

        await self.messages.create_index(
            "message_id",
            unique=True,
        )

        await self.messages.create_index(
            [
                ("chat_id", 1),
                ("created_at", 1),
            ]
        )

        await self.messages.create_index(
            [
                ("user_id", 1),
                ("created_at", -1),
            ]
        )

    async def ensure_chat(
        self,
        *,
        chat_id: Optional[str],
        user_id: str,
        initial_title: str,
        report_name: Optional[str],
    ) -> Dict[str, Any]:
        """
        Return an existing user-owned chat or create a new one.
        """

        if chat_id:
            existing = await self.chats.find_one(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
                {
                    "_id": 0,
                },
            )

            if existing is None:
                raise ValueError(
                    "The requested chat does not exist for this user."
                )

            updates: Dict[str, Any] = {
                "updated_at": utc_now(),
            }

            existing_report = clean_report_name(
                existing.get("report_name")
            )
            incoming_report = clean_report_name(report_name)

            if (
                existing_report
                and incoming_report
                and not same_report_name(
                    existing_report,
                    incoming_report,
                )
            ):
                raise ValueError(
                    f"This chat is locked to report '{existing_report}' and "
                    f"cannot be changed to '{incoming_report}'."
                )

            # A chat becomes bound when the first non-empty report is used.
            # Once bound, never overwrite that report.
            if not existing_report and incoming_report:
                updates["report_name"] = incoming_report

            await self.chats.update_one(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
                {
                    "$set": updates,
                },
            )

            existing.update(updates)
            return existing

        now = utc_now()
        new_chat_id = str(uuid4())

        document = {
            "chat_id": new_chat_id,
            "user_id": user_id,
            "title": build_chat_title(initial_title),
            "report_name": clean_report_name(report_name),
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
        }

        await self.chats.insert_one(document)

        return {
            key: value
            for key, value in document.items()
            if key != "_id"
        }

    async def list_chats(
        self,
        *,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        cursor = (
            self.chats.find(
                {
                    "user_id": user_id,
                },
                {
                    "_id": 0,
                },
            )
            .sort("updated_at", -1)
            .limit(max(1, min(limit, 100)))
        )

        return [document async for document in cursor]

    async def get_chat(
        self,
        *,
        chat_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.chats.find_one(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
            {
                "_id": 0,
            },
        )

    async def delete_chat(
        self,
        *,
        chat_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Delete one user-owned conversation and all of its messages.

        Ownership is enforced by both chat_id and user_id so one user
        cannot delete another user's conversation.
        """

        delete_chat_result = await self.chats.delete_one(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

        if delete_chat_result.deleted_count == 0:
            return None

        delete_messages_result = await self.messages.delete_many(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

        return {
            "chat_id": chat_id,
            "deleted_messages": int(
                delete_messages_result.deleted_count
            ),
        }


    async def add_message(
        self,
        *,
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
        report_name: Optional[str] = None,
        variables: Optional[List[Dict[str, Any]]] = None,
        reply_to_message_id: Optional[str] = None,
        source_user_message_id: Optional[str] = None,
        workflow_used: Optional[str] = None,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        result_count: int = 0,
        table_rows: Optional[List[Dict[str, Any]]] = None,
        sql: Optional[str] = None,
        sql_template: Optional[str] = None,
        query_executed: Optional[bool] = None,
        action: Optional[str] = None,
        selected_example: Optional[int] = None,
        faiss_examples_used: Optional[int] = None,
        request_mode: Optional[str] = None,
        variable_names: Optional[List[str]] = None,
        variable_values: Optional[Dict[str, Any]] = None,
        query_without_values: Optional[str] = None,
        feedback_reexecution: Optional[Dict[str, Any]] = None,
        original_feedback_input: Optional[str] = None,
        normalized_feedback_input: Optional[str] = None,
        normalized_input: Optional[str] = None,
        resolved_user_input: Optional[str] = None,
        resolved_normalized_input: Optional[str] = None,
    ) -> Dict[str, Any]:
        if role not in {"user", "assistant"}:
            raise ValueError("Message role must be user or assistant.")

        cleaned_content = str(content or "").strip()

        if not cleaned_content:
            raise ValueError("Message content cannot be empty.")

        chat = await self.get_chat(
            chat_id=chat_id,
            user_id=user_id,
        )
        if chat is None:
            raise ValueError("The requested chat does not exist for this user.")

        existing_report = clean_report_name(chat.get("report_name"))
        incoming_report = clean_report_name(report_name)

        if (
            existing_report
            and incoming_report
            and not same_report_name(existing_report, incoming_report)
        ):
            raise ValueError(
                f"This chat is locked to report '{existing_report}' and "
                f"cannot be changed to '{incoming_report}'."
            )

        effective_report = existing_report or incoming_report

        now = utc_now()
        message_id = str(uuid4())

        document = {
            "message_id": message_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "role": role,
            "content": cleaned_content,
            "normalized_input": (
                str(normalized_input).strip()
                if normalized_input is not None
                else None
            ),
            "report_name": effective_report,
            "variables": list(variables or []),
            "reply_to_message_id": reply_to_message_id,
            "source_user_message_id": source_user_message_id,
            "workflow_used": workflow_used,
            "workflow_id": workflow_id,
            "status": status,
            "stage": stage,
            "result_count": int(result_count or 0),
            "table_rows": list(table_rows or []),
            "sql": sql,
            "sql_template": sql_template,
            "query_executed": (
                bool(query_executed)
                if query_executed is not None
                else False
            ),
            "action": action,
            "selected_example": selected_example,
            "faiss_examples_used": int(faiss_examples_used or 0),
            "request_mode": request_mode,
            "variable_names": list(variable_names or []),
            "variable_values": dict(variable_values or {}),
            "query_without_values": query_without_values,
            "feedback_reexecution": dict(feedback_reexecution or {}),
            "original_feedback_input": original_feedback_input,
            "normalized_feedback_input": normalized_feedback_input,
            "resolved_user_input": (
                str(resolved_user_input).strip()
                if resolved_user_input is not None
                else None
            ),
            "resolved_normalized_input": (
                str(resolved_normalized_input).strip()
                if resolved_normalized_input is not None
                else None
            ),
            "feedback": None,
            "created_at": now,
            "updated_at": now,
        }

        await self.messages.insert_one(document)

        chat_updates: Dict[str, Any] = {
            "updated_at": now,
            "last_message": cleaned_content,
        }
        if effective_report:
            chat_updates["report_name"] = effective_report

        await self.chats.update_one(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
            {
                "$set": chat_updates,
                "$inc": {
                    "message_count": 1,
                },
            },
        )

        return {
            key: value
            for key, value in document.items()
            if key != "_id"
        }

    
    async def edit_user_message(
        self,
        *,
        message_id: str,
        user_id: str,
        content: str,
    ) -> Optional[Dict[str, Any]]:
        cleaned_content = str(content or "").strip()

        if not cleaned_content:
            raise ValueError("Message content cannot be empty.")

        message = await self.get_message(
            message_id=message_id,
            user_id=user_id,
        )

        if message is None:
            return None

        if message.get("role") != "user":
            raise ValueError("Only user messages can be edited.")

        chat_id = str(message.get("chat_id") or "")

        chat = await self.get_chat(
            chat_id=chat_id,
            user_id=user_id,
        )

        if chat is None:
            return None

        now = utc_now()

        result = await self.messages.update_one(
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "user",
            },
            {
                "$set": {
                    "content": cleaned_content,
                    "is_edited": True,
                    "edited_at": now,
                    "updated_at": now,
                }
            },
        )

        if result.matched_count == 0:
            return None

        # Update the chat preview only if this is still its latest message.
        latest = await self.messages.find_one(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
            {"_id": 0, "message_id": 1},
            sort=[("created_at", -1)],
        )

        chat_updates = {"updated_at": now}

        if latest and latest.get("message_id") == message_id:
            chat_updates["last_message"] = cleaned_content

        await self.chats.update_one(
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
            {"$set": chat_updates},
        )

        updated = await self.get_message(
            message_id=message_id,
            user_id=user_id,
        )

        return updated

    
    async def get_assistant_for_user_message(
        self,
        *,
        user_message_id: str,
        user_id: str,
        chat_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the assistant response associated with one user message."""

        return await self.messages.find_one(
            {
                "source_user_message_id": user_message_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "assistant",
            },
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    
    async def update_assistant_for_user_message(
        self,
        *,
        user_message_id: str,
        user_id: str,
        chat_id: str,
        fields: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update the existing assistant response for an edited user message."""

        existing = await self.get_assistant_for_user_message(
            user_message_id=user_message_id,
            user_id=user_id,
            chat_id=chat_id,
        )

        if existing is None:
            return None

        message_id = existing["message_id"]

        await self.messages.update_one(
            {
                "message_id": message_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "role": "assistant",
                "source_user_message_id": user_message_id,
            },
            {
                "$set": fields,
                "$unset": {"feedback": ""},
            },
        )

        return await self.messages.find_one(
            {
                "message_id": message_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "role": "assistant",
            },
            {"_id": 0},
        )


    async def list_messages(
        self,
        *,
        chat_id: str,
        user_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        chat = await self.get_chat(
            chat_id=chat_id,
            user_id=user_id,
        )

        if chat is None:
            raise ValueError(
                "The requested chat does not exist for this user."
            )

        cursor = (
            self.messages.find(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
                {
                    "_id": 0,
                },
            )
            .sort("created_at", 1)
            .limit(max(1, min(limit, 500)))
        )

        return [document async for document in cursor]

    async def get_message(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.messages.find_one(
            {
                "message_id": message_id,
                "user_id": user_id,
            },
            {
                "_id": 0,
            },
        )

    async def get_recent_memory(
        self,
        *,
        chat_id: str,
        user_id: str,
        limit: int = 6,
    ) -> List[Dict[str, str]]:
        """
        Return recent conversation messages in the shape expected by
        IntentClassifier: [{"role": "user|assistant", "content": "..."}].
        """

        cursor = (
            self.messages.find(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": {
                        "$in": [
                            "user",
                            "assistant",
                        ]
                    },
                },
                {
                    "_id": 0,
                    "role": 1,
                    "content": 1,
                    "created_at": 1,
                },
            )
            .sort("created_at", -1)
            .limit(max(1, min(limit, 20)))
        )

        newest_first = [document async for document in cursor]
        newest_first.reverse()

        return [
            {
                "role": str(item["role"]),
                "content": str(item["content"]),
            }
            for item in newest_first
            if item.get("content")
        ]

    async def get_recent_context(
        self,
        *,
        chat_id: str,
        user_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Return richer recent conversation context for contextual
        follow-up resolution.

        Unlike get_recent_memory(), this keeps message IDs, variables,
        report name and reply/source links.
        """

        cursor = (
            self.messages.find(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": {
                        "$in": [
                            "user",
                            "assistant",
                        ]
                    },
                },
                {
                    "_id": 0,
                    "message_id": 1,
                    "role": 1,
                    "content": 1,
                    "report_name": 1,
                    "variables": 1,
                    "reply_to_message_id": 1,
                    "source_user_message_id": 1,
                    "workflow_used": 1,
                    "status": 1,
                    "stage": 1,
                    "created_at": 1,
                },
            )
            .sort("created_at", -1)
            .limit(max(1, min(limit, 30)))
        )

        newest_first = [
            document
            async for document in cursor
        ]
        newest_first.reverse()

        return newest_first

    async def get_reply_context(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve the message being replied to and, when possible, the
        user request that produced that assistant response.

        New assistant messages store source_user_message_id directly.
        For older messages, the nearest previous user message in the
        same chat is used as a compatibility fallback.
        """

        target = await self.get_message(
            message_id=message_id,
            user_id=user_id,
        )

        if target is None:
            return None

        source_user_message = None

        if target.get("role") == "user":
            source_user_message = target

        elif target.get("role") == "assistant":
            source_user_message_id = str(
                target.get(
                    "source_user_message_id"
                )
                or ""
            ).strip()

            if source_user_message_id:
                source_user_message = (
                    await self.get_message(
                        message_id=(
                            source_user_message_id
                        ),
                        user_id=user_id,
                    )
                )

            if source_user_message is None:
                target_created_at = target.get(
                    "created_at"
                )

                if target_created_at is not None:
                    cursor = (
                        self.messages.find(
                            {
                                "chat_id": target.get(
                                    "chat_id"
                                ),
                                "user_id": user_id,
                                "role": "user",
                                "created_at": {
                                    "$lte": target_created_at,
                                },
                            },
                            {
                                "_id": 0,
                            },
                        )
                        .sort("created_at", -1)
                        .limit(1)
                    )

                    async for item in cursor:
                        source_user_message = item
                        break

        return {
            "target_message": target,
            "source_user_message": (
                source_user_message
            ),
        }


    async def get_result_analysis_context(
        self,
        *,
        chat_id: str,
        user_id: str,
        preferred_message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the most relevant previously stored assistant
        result for result-analysis questions.

        If the user explicitly replied to an assistant message,
        that message is preferred when it contains table rows.

        Otherwise, use the most recent assistant message in the
        same chat that contains stored result rows.
        """

        result_message = None

        # ====================================================
        # EXPLICIT REPLY TARGET
        # ====================================================

        if preferred_message_id:

            candidate = (
                await self.messages.find_one(
                    {
                        "message_id": (
                            preferred_message_id
                        ),
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "role": "assistant",
                    },
                    {
                        "_id": 0,
                    },
                )
            )

            if (
                candidate
                and candidate.get(
                    "table_rows"
                )
            ):
                result_message = (
                    candidate
                )

        # ====================================================
        # LATEST ASSISTANT RESULT
        # ====================================================

        if result_message is None:

            cursor = (
                self.messages.find(
                    {
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "role": "assistant",

                        # Non-empty table_rows array.
                        "table_rows.0": {
                            "$exists": True,
                        },
                    },
                    {
                        "_id": 0,
                    },
                )
                .sort(
                    "created_at",
                    -1,
                )
                .limit(1)
            )

            async for item in cursor:
                result_message = item
                break

        if result_message is None:
            return None

        # ====================================================
        # FIND ORIGINAL USER REQUEST
        # ====================================================

        source_user_message = None

        source_user_message_id = str(
            result_message.get(
                "source_user_message_id"
            )
            or ""
        ).strip()

        if source_user_message_id:

            source_user_message = (
                await self.messages.find_one(
                    {
                        "message_id": (
                            source_user_message_id
                        ),
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "role": "user",
                    },
                    {
                        "_id": 0,
                        "message_id": 1,
                        "content": 1,
                        "report_name": 1,
                        "variables": 1,
                    },
                )
            )

        return {
            "assistant_message_id": (
                result_message.get(
                    "message_id"
                )
            ),

            "assistant_message": (
                result_message.get(
                    "content"
                )
            ),

            "source_user_message_id": (
                source_user_message_id
                or None
            ),

            "source_user_input": (
                source_user_message.get(
                    "content"
                )
                if source_user_message
                else None
            ),

            "report_name": (
                result_message.get(
                    "report_name"
                )
            ),

            "workflow_used": (
                result_message.get(
                    "workflow_used"
                )
            ),

            "result_count": int(
                result_message.get(
                    "result_count",
                    0,
                )
                or 0
            ),

            "table_rows": list(
                result_message.get(
                    "table_rows"
                )
                or []
            ),
        }



    async def set_feedback(
        self,
        *,
        message_id: str,
        user_id: str,
        rating: str,
    ) -> Optional[Dict[str, Any]]:
        if rating not in {"up", "down"}:
            raise ValueError("Feedback rating must be up or down.")

        message = await self.get_message(
            message_id=message_id,
            user_id=user_id,
        )

        if message is None:
            return None

        if message.get("role") != "assistant":
            raise ValueError(
                "Feedback can only be attached to assistant messages."
            )

        now = utc_now()

        await self.messages.update_one(
            {
                "message_id": message_id,
                "user_id": user_id,
            },
            {
                "$set": {
                    "feedback": rating,
                    "feedback_updated_at": now,
                    "updated_at": now,
                }
            },
        )

        message["feedback"] = rating
        message["feedback_updated_at"] = now
        message["updated_at"] = now

        return message

    async def close(self) -> None:
        await self.client.close()
