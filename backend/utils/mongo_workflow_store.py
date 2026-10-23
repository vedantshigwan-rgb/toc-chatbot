from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MongoWorkflowStore:
    def __init__(
        self,
        mongo_uri: str,
        database_name: str = "toc",
        collection_name: str = "workflows",
    ) -> None:
        self.client = AsyncMongoClient(mongo_uri)

        self.database = self.client[database_name]

        self.collection = self.database[
            collection_name
        ]

    async def initialize(self) -> None:
        """
        Verify the MongoDB connection and create required indexes.
        """
        await self.client.admin.command("ping")

        await self.collection.create_index(
            "workflow_id",
            unique=True,
        )

        await self.collection.create_index(
            [
                ("session_id", 1),
                ("created_at", -1),
            ]
        )

        await self.collection.create_index(
            [
                ("user_id", 1),
                ("created_at", -1),
            ]
        )

        await self.collection.create_index(
            "status"
        )

    async def create_workflow(
        self,
        *,
        workflow_id: str,
        session_id: Optional[str],
        chat_id: Optional[str],
        user_id: str,
        user_role: Optional[str],
        report_name: str,
        user_input: str,
        query_without_values: str,
        variable_names: List[str],
        variables_key: str,
        variables: Dict[str, Any],
        suggestions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        now = utc_now()

        document = {
            "workflow_id": workflow_id,
            "session_id": session_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_role": user_role,

            "workflow_type": "primary",
            "status": "clarification_required",

            "report_name": report_name,
            "user_input": user_input,
            "normalized_input": query_without_values,
            "query_without_values": (
                query_without_values
            ),

            "variable_names": variable_names,
            "variables_key": variables_key,
            "variables": variables,

            "suggestions": suggestions,

            "fallback_generation": {
                "status": "generating",
                "generated_sql": None,
                "explanation": None,
                "used_variables": [],
                "generated_at": None,
                "error": None,
            },

            "selected_option_id": None,
            "selected_display_question": None,
            "selection_type": None,

            "sql_source": None,
            "final_sql": None,

            "sql_validation": {
                "status": "pending",
                "is_select_only": None,
                "variables_valid": None,
                "tables_valid": None,
                "columns_valid": None,
                "validation_errors": [],
            },

            "execution": {
                "status": "not_started",
                "execution_time_ms": None,
                "table_data": [],
            },

            "sql_error": None,
            "retry_count": 0,

            "created_at": now,
            "updated_at": now,
        }

        try:
            await self.collection.insert_one(document)
        except PyMongoError as error:
            raise RuntimeError(
                "Failed to create workflow document."
            ) from error

        document.pop("_id", None)

        return document

    
    async def store_fallback_result(
        self,
        *,
        workflow_id: str,
        fallback_result: Dict[str, Any],
    ) -> bool:
        now = utc_now()

        generated_sql = (
            fallback_result.get("generated_sql")
            or fallback_result.get("sql")
        )

        executable_sql = (
            fallback_result.get("executable_sql")
            or fallback_result.get("sql")
        )

        update_fields = {
            # Preserve existing fallback-generation behavior.
            "fallback_generation.status": "ready",
            "fallback_generation.generated_sql": generated_sql,
            "fallback_generation.explanation": (
                fallback_result.get("explanation")
            ),
            "fallback_generation.used_variables": (
                fallback_result.get("used_variables", [])
            ),
            "fallback_generation.generated_at": now,
            "fallback_generation.error": None,

            # Store the original SQL for future page requests.
            "final_sql": executable_sql,
            "sql_source": fallback_result.get("sql_source"),

            # Preserve execution metadata.
            "execution.status": fallback_result.get(
                "status",
                "completed",
            ),
            "execution.execution_time_ms": (
                fallback_result.get("execution_time_ms")
            ),
            "execution.result_count": fallback_result.get(
                "result_count",
                0,
            ),
            "execution.table_data": fallback_result.get(
                "results",
                [],
            ),

            "sql_error": fallback_result.get("error"),
            "updated_at": now,
        }

        update_result = await self.collection.update_one(
            {
                "workflow_id": workflow_id,
            },
            {
                "$set": update_fields,
            },
        )

        return update_result.matched_count == 1


    async def store_fallback_error(
        self,
        *,
        workflow_id: str,
        error_message: str,
    ) -> bool:
        update_result = await self.collection.update_one(
            {
                "workflow_id": workflow_id,
            },
            {
                "$set": {
                    "fallback_generation.status": (
                        "failed"
                    ),
                    "fallback_generation.error": (
                        error_message
                    ),
                    "updated_at": utc_now(),
                }
            },
        )

        return update_result.matched_count == 1

    
    async def get_workflow(
        self,
        workflow_id: str,
    ) -> Optional[Dict[str, Any]]:
        workflow = await self.collection.find_one(
            {
                "workflow_id": workflow_id,
            },
            {
                "_id": 0,
            },
        )

        return workflow

    async def get_workflow_for_user(
        self,
        workflow_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a workflow only if it belongs to the requesting user.
        """
        workflow = await self.collection.find_one(
            {
                "workflow_id": workflow_id,
                "user_id": user_id,
            },
            {
                "_id": 0,
            },
        )

        return workflow

    async def close(self) -> None:
        await self.client.close()
