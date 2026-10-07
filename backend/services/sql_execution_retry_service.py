from __future__ import annotations

import asyncio
import hashlib
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
)

from core.enums import RecoveryAction
from core.models import (
    PrimaryExecutionAttempt,
    PrimaryExecutionRetryResult,
    SQLExecutionResult,
)
from diagnostics.oracle_error_classifier import (
    classify_oracle_error,
)

if TYPE_CHECKING:
    # Import only for type checking - avoids a runtime circular
    # import, since orchestration.request_db_execution_budget itself
    # imports DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS from this module
    # (via the services.primary_execution_retry_service compatibility
    # re-export).
    from orchestration.request_db_execution_budget import (
        RequestDatabaseExecutionTracker,
    )


SQLExecutor = Callable[
    ...,
    Awaitable[SQLExecutionResult],
]

SleepFunction = Callable[
    [float],
    Awaitable[None],
]


_RETRY_ACTIONS = {
    RecoveryAction.RETRY_EXECUTION.value,
    RecoveryAction.RETRY_CONNECTION.value,
}

# Step 21: centralized so the request-level DB execution cap can
# derive its default without duplicating this magic number, and so
# every caller shares one source of truth.
DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS = 3

REQUEST_DB_EXECUTION_LIMIT_REACHED_CODE = (
    "request_db_execution_limit_reached"
)


def _sql_hash(sql: str) -> str:
    """
    Produce a stable hash so we can prove every infrastructure
    retry used exactly the same SQL.
    """

    return hashlib.sha256(
        sql.encode("utf-8")
    ).hexdigest()


def _action_value(action) -> str:
    """
    Support both enum and string values because ContractModel
    serializes enum values as strings.
    """

    return getattr(
        action,
        "value",
        str(action),
    )


async def execute_sql_with_retry(
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
    reason_code: str = "business_query",
    source_stage: str = "sql_execution_retry",
) -> PrimaryExecutionRetryResult:
    """
    STEP 35: workflow-agnostic bounded infrastructure retry engine for
    business SQL execution. Extracted verbatim (same rules, same
    behavior) from what was previously
    services.primary_execution_retry_service.execute_primary_sql_with_retry
    - that function is now a thin compatibility wrapper around this
    one, supplying Primary's own reason_code/source_stage. This is the
    ONE shared same-SQL transient retry engine for both Primary and
    Secondary; there is no separate execute_secondary_sql_with_retry.

    Rules:

    1. Every retry uses the exact same SQL.
    2. Only transient Oracle connection/execution failures retry.
    3. SQL syntax and metadata errors do not retry here.
    4. Authentication and permission failures do not retry.
    5. This service never repairs or regenerates SQL.

    `binds` is optional and defaults to no bind parameters,
    matching every existing caller's current behavior. When
    supplied, the same bind values are reused on every retry
    attempt, alongside the same SQL text.

    `db_execution_tracker` is optional (Step 21). When supplied, one
    execution slot is reserved immediately before each actual Oracle
    call - including every same-SQL transient retry. If the tracker
    is already exhausted, the executor is never called: the loop
    stops immediately and a controlled, non-retryable
    "request_db_execution_limit_reached" result is returned instead
    of raising. This is a request-level safety stop, not an Oracle
    retry-budget exhaustion, and it never consumes any workflow's
    separate semantic-recovery budget.

    `reason_code`/`source_stage` are attached to each DB-execution-
    tracker reservation only (safe diagnostics, never SQL text) so a
    persisted request_db_execution snapshot can distinguish which
    caller/workflow issued a given reservation.
    """

    executable_sql = str(
        sql or ""
    ).strip()

    if max_attempts < 1:
        raise ValueError(
            "max_attempts must be at least 1."
        )

    if base_delay_seconds < 0:
        raise ValueError(
            "base_delay_seconds cannot be negative."
        )

    if max_delay_seconds < 0:
        raise ValueError(
            "max_delay_seconds cannot be negative."
        )

    # Lazy import keeps isolated tests independent from the
    # installed Oracle client.
    if executor is None:
        from services.sql_execution_service import (
            execute_sql_query,
        )

        executor = execute_sql_query

    attempts: list[
        PrimaryExecutionAttempt
    ] = []

    statement_hash = _sql_hash(
        executable_sql
    )

    final_result: (
        SQLExecutionResult | None
    ) = None

    exhausted = False
    db_cap_exhausted = False

    for attempt_number in range(
        1,
        max_attempts + 1,
    ):
        if db_execution_tracker is not None:
            reservation = db_execution_tracker.try_reserve(
                reason_code=reason_code,
                source_stage=source_stage,
            )

            if not reservation.granted:
                final_result = SQLExecutionResult(
                    success=False,
                    query_executed=False,
                    sql=executable_sql,
                    row_limit=row_limit,
                    result_count=0,
                    results=[],
                    error_kind="request_safety",
                    error_code=(
                        REQUEST_DB_EXECUTION_LIMIT_REACHED_CODE
                    ),
                    error_message=(
                        "The database execution safety limit was "
                        "reached for this request."
                    ),
                    error_details={
                        "max_executions": (
                            reservation.state.max_executions
                        ),
                        "executions_used": (
                            reservation.state.executions_used
                        ),
                    },
                    exception_type=None,
                )

                db_cap_exhausted = True

                break

        execution_result = await executor(
            sql=executable_sql,
            row_limit=row_limit,
            binds=binds,
        )

        final_result = execution_result

        oracle_classification = None
        retry_scheduled = False
        retry_delay_seconds = 0.0

        # Successful execution ends the retry cycle.
        if execution_result.success:
            attempts.append(
                PrimaryExecutionAttempt(
                    attempt_number=(
                        attempt_number
                    ),
                    sql_hash=statement_hash,
                    execution_result=(
                        execution_result
                    ),
                )
            )

            break

        # Only Oracle failures are classified for retry.
        if (
            execution_result.error_kind
            == "oracle"
        ):
            oracle_classification = (
                classify_oracle_error(
                    code=(
                        execution_result
                        .error_code
                    ),
                    message=(
                        execution_result
                        .error_message
                    ),
                    details=(
                        execution_result
                        .error_details
                    ),
                )
            )

            action = _action_value(
                oracle_classification
                .recommended_action
            )

            retry_allowed = (
                oracle_classification.retryable
                and action in _RETRY_ACTIONS
            )

            if (
                retry_allowed
                and attempt_number
                < max_attempts
            ):
                retry_scheduled = True

                retry_delay_seconds = min(
                    base_delay_seconds
                    * (
                        2
                        ** (
                            attempt_number - 1
                        )
                    ),
                    max_delay_seconds,
                )

            elif (
                retry_allowed
                and attempt_number
                >= max_attempts
            ):
                exhausted = True

        attempts.append(
            PrimaryExecutionAttempt(
                attempt_number=attempt_number,
                sql_hash=statement_hash,
                execution_result=(
                    execution_result
                ),
                oracle_classification=(
                    oracle_classification
                ),
                retry_scheduled=(
                    retry_scheduled
                ),
                retry_delay_seconds=(
                    retry_delay_seconds
                ),
            )
        )

        if not retry_scheduled:
            break

        if retry_delay_seconds > 0:
            await sleep_function(
                retry_delay_seconds
            )

    if final_result is None:
        raise RuntimeError(
            "SQL execution produced no result."
        )

    return PrimaryExecutionRetryResult(
        success=final_result.success,
        final_result=final_result,
        attempt_count=len(attempts),
        exhausted=exhausted,
        db_cap_exhausted=db_cap_exhausted,
        attempts=attempts,
    )
