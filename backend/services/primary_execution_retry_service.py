from __future__ import annotations

import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Optional,
)

from core.models import (
    PrimaryExecutionRetryResult,
)
from services.sql_execution_retry_service import (
    DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS,
    REQUEST_DB_EXECUTION_LIMIT_REACHED_CODE,
    SleepFunction,
    SQLExecutor,
    execute_sql_with_retry,
)

if TYPE_CHECKING:
    from orchestration.request_db_execution_budget import (
        RequestDatabaseExecutionTracker,
    )


# ================================================================
# STEP 35: this module is now a thin, behavior-preserving
# compatibility wrapper. The actual bounded same-SQL transient retry
# engine was extracted, unchanged, to
# services.sql_execution_retry_service.execute_sql_with_retry - the
# ONE shared execution-retry engine both Primary and Secondary use.
# `DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS` and
# `REQUEST_DB_EXECUTION_LIMIT_REACHED_CODE` are re-exported here
# unchanged so every existing import of this module keeps working
# without modification.
# ================================================================


async def execute_primary_sql_with_retry(
    *,
    sql: str,
    row_limit: int = 50,
    max_attempts: int = DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 4.0,
    executor: SQLExecutor | None = None,
    sleep_function: SleepFunction = asyncio.sleep,
    binds: Optional[Dict[str, Any]] = None,
    db_execution_tracker: Optional[
        "RequestDatabaseExecutionTracker"
    ] = None,
) -> PrimaryExecutionRetryResult:
    """
    Execute primary-workflow SQL with bounded infrastructure retries.

    Compatibility wrapper (Step 35): delegates to the shared, workflow-
    agnostic services.sql_execution_retry_service.execute_sql_with_retry
    with Primary's own reason_code/source_stage, preserving this
    function's exact original signature and behavior for every existing
    caller.
    """

    return await execute_sql_with_retry(
        sql=sql,
        row_limit=row_limit,
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        executor=executor,
        sleep_function=sleep_function,
        binds=binds,
        db_execution_tracker=db_execution_tracker,
        reason_code="primary_business_query",
        source_stage="primary_execution_retry",
    )
