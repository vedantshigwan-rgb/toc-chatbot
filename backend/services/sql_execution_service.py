from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from agents.query_executor import run_sql
from core.errors import OracleQueryError
from core.models import SQLExecutionResult


async def execute_sql_query(
    *,
    sql: str,
    row_limit: int = 50,
    binds: Optional[Dict[str, Any]] = None,
) -> SQLExecutionResult:
    """
    Execute SQL through the existing Oracle query executor.

    This service:

    1. Runs the synchronous Oracle operation in a worker thread.
    2. Preserves Oracle error codes and metadata.
    3. Distinguishes Oracle errors from unexpected errors.
    4. Treats zero returned rows as successful execution.
    5. Returns one structured execution result.

    `binds` is optional and defaults to no bind parameters,
    matching every existing caller's current behavior. When
    supplied, it is passed straight through to the Oracle
    executor's own bind-parameter support so a caller can safely
    parameterize a user-supplied value instead of concatenating
    it into the SQL text.

    Oracle retryability and routing are intentionally not decided
    here. That will be handled by the Oracle error classifier.
    """

    executable_sql = str(sql or "").strip()

    if not executable_sql:
        return SQLExecutionResult(
            success=False,
            query_executed=False,
            sql="",
            row_limit=row_limit,
            result_count=0,
            results=[],
            error_kind="validation",
            error_code="empty_sql",
            error_message="Executable SQL is empty.",
            error_details={},
            exception_type=None,
        )

    if row_limit <= 0:
        return SQLExecutionResult(
            success=False,
            query_executed=False,
            sql=executable_sql,
            row_limit=row_limit,
            result_count=0,
            results=[],
            error_kind="validation",
            error_code="invalid_row_limit",
            error_message=(
                "The SQL row limit must be greater than zero."
            ),
            error_details={
                "row_limit": row_limit,
            },
            exception_type=None,
        )

    try:
        query_results = await asyncio.to_thread(
            run_sql,
            executable_sql,
            row_limit,
            binds,
        )

    except OracleQueryError as error:
        return SQLExecutionResult(
            success=False,
            query_executed=False,
            sql=executable_sql,
            row_limit=row_limit,
            result_count=0,
            results=[],
            error_kind="oracle",
            error_code=error.code,
            error_message=error.message,
            error_details=dict(
                error.details or {}
            ),
            exception_type=type(error).__name__,
        )

    except Exception as error:
        return SQLExecutionResult(
            success=False,
            query_executed=False,
            sql=executable_sql,
            row_limit=row_limit,
            result_count=0,
            results=[],
            error_kind="execution",
            error_code=None,
            error_message=str(error),
            error_details={},
            exception_type=type(error).__name__,
        )

    if query_results is None:
        query_results = []

    if not isinstance(query_results, list):
        return SQLExecutionResult(
            success=False,
            query_executed=True,
            sql=executable_sql,
            row_limit=row_limit,
            result_count=0,
            results=[],
            error_kind="execution",
            error_code="invalid_result_type",
            error_message=(
                "The Oracle query executor returned an invalid "
                "result type."
            ),
            error_details={
                "result_type": (
                    type(query_results).__name__
                ),
            },
            exception_type=None,
        )

    normalized_results: List[Dict[str, Any]] = []

    for row_number, row in enumerate(
        query_results,
        start=1,
    ):
        if not isinstance(row, dict):
            return SQLExecutionResult(
                success=False,
                query_executed=True,
                sql=executable_sql,
                row_limit=row_limit,
                result_count=0,
                results=[],
                error_kind="execution",
                error_code="invalid_result_row",
                error_message=(
                    "The Oracle query executor returned a "
                    "non-dictionary result row."
                ),
                error_details={
                    "row_number": row_number,
                    "row_type": type(row).__name__,
                },
                exception_type=None,
            )

        normalized_results.append(row)

    return SQLExecutionResult(
        success=True,
        query_executed=True,
        sql=executable_sql,
        row_limit=row_limit,
        result_count=len(normalized_results),
        results=normalized_results,
        error_kind=None,
        error_code=None,
        error_message=None,
        error_details={},
        exception_type=None,
    )