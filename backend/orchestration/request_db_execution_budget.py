from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, ConfigDict

from orchestration.primary_semantic_recovery_budget import (
    DEFAULT_PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS,
)
from services.primary_execution_retry_service import (
    DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS,
)

# Step 21 validation fix: within a SINGLE semantic attempt, today's
# already-wired automatic SQL repair mechanism
# (workflows/primary_workflow.py - "Automatic SQL repair is attempted
# only once") can run the FULL schema-validation + business-retry
# cycle twice - once for the initial generated SQL, once more for the
# repaired SQL - before any future semantic-recovery attempt is even
# considered. The original Step 21 default ignored this repair-path
# doubling entirely, so a single ordinary request already had less
# headroom than the formula implied.
SQL_VARIANTS_PER_SEMANTIC_ATTEMPT = 2

# Step 21 validation fix: Oracle schema validation issues one
# metadata query per DISTINCT physical table referenced in the
# generated SQL (services/oracle_schema_validator.py). There is no
# hard limit anywhere in the codebase on how many tables a generated
# report SQL can reference, so this is a documented TYPICAL
# assumption used only to derive a sane out-of-the-box default - it
# is NOT an enforced ceiling. The tracker still counts every real
# schema-validation call regardless of this constant; a request
# against a wider join legitimately consumes more of the cap
# headroom, and that is expected, safe behavior.
TYPICAL_SCHEMA_VALIDATION_TABLE_COUNT = 3

# Step 21 validation fix: derived worst-case default, now honestly
# covering the two BOUNDED Oracle-calling dimensions that share this
# tracker:
#
#   (1 + semantic_max)              - the initial semantic attempt
#                                      plus every additional semantic
#                                      recovery attempt currently
#                                      allowed (still unwired in
#                                      production - see this module's
#                                      class docstrings)
#   * SQL_VARIANTS_PER_SEMANTIC_ATTEMPT
#                                    - the initial SQL variant plus
#                                      today's already-wired one-shot
#                                      automatic repair variant
#   * (oracle_max + TYPICAL_SCHEMA_VALIDATION_TABLE_COUNT)
#                                    - per SQL variant: up to
#                                      oracle_max same-SQL business
#                                      retries, plus the typical
#                                      schema-validation table count
#
# DELIBERATELY EXCLUDED from this derivation: zero-row value
# validation (services/value_validator.py). Its Oracle-call count is
# proportional to how many values the USER submitted (up to 2 calls -
# an existence check plus an optional suggestion query - per
# submitted value), with no cap anywhere in the request pipeline. No
# finite constant can honestly bound that, so it is not part of the
# derived default. Every real value-validation Oracle call still
# consumes this SAME tracker at runtime (never excluded from
# counting) - a request that submits enough values to run past the
# cap is expected to hit request_db_execution_limit_reached, and that
# is this safety mechanism working as intended, not a defect. Raise
# REQUEST_DB_MAX_EXECUTIONS explicitly if a deployment's normal
# traffic legitimately needs more headroom.
DEFAULT_REQUEST_DB_MAX_EXECUTIONS = (
    (1 + DEFAULT_PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS)
    * SQL_VARIANTS_PER_SEMANTIC_ATTEMPT
    * (
        DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS
        + TYPICAL_SCHEMA_VALIDATION_TABLE_COUNT
    )
)


class RequestDatabaseExecutionState(BaseModel):
    """
    Typed, JSON-safe snapshot of one request-scoped DB-execution
    tracker at a point in time. Safe for model_dump(mode="json") -
    never carries SQL text, Oracle errors, prompts, or other private
    data.
    """

    model_config = ConfigDict(extra="forbid")

    max_executions: int
    executions_used: int = 0
    executions_remaining: int
    exhausted: bool = False
    last_execution_reason: Optional[str] = None


class RequestDatabaseExecutionReservation(BaseModel):
    """
    Result of one try_reserve() call. `granted=False` means the
    tracker was already exhausted and NO execution slot was consumed
    - the caller must not issue the Oracle call it was about to make.
    """

    model_config = ConfigDict(extra="forbid")

    granted: bool
    execution_number: Optional[int] = None
    reason_code: Optional[str] = None
    source_stage: Optional[str] = None
    state: RequestDatabaseExecutionState


class RequestDatabaseExecutionTracker:
    """
    Step 21: mutable, REQUEST-SCOPED tracker for how many actual
    Oracle executions this logical Primary request has issued.

    This is a plain Python object - it must be constructed fresh per
    logical request (via RequestDatabaseExecutionPolicy.new_tracker())
    and held only as a local variable for the lifetime of that
    request, exactly like PrimarySemanticRecoveryBudget. It must NEVER
    be stored on app.state, a WorkflowManager instance, a module-level
    global, or any other object shared across concurrent requests.

    Unlike PrimarySemanticRecoveryBudget (which counts additional
    MEANING-changing Primary attempts), this tracker counts every
    ACTUAL Oracle execution within the logical request - the business
    SQL query, any same-SQL transient Oracle retry of it, Oracle
    schema-validation metadata lookups, and value-validation
    diagnostic queries. It does NOT count semantic-checker calls
    (those never touch Oracle) or the mere creation of a
    PrimarySemanticRecoveryBudget/RecoveryDecision.

    A DB execution slot is consumed immediately BEFORE the actual
    Oracle call begins - never merely because a caller is about to
    decide whether to call Oracle. Once reserved, the slot remains
    consumed even if that Oracle call itself later fails (timeout,
    Oracle error, transport error) - reservations are never refunded,
    so this remains a hard, monotonic safety ceiling.
    """

    def __init__(
        self,
        *,
        max_executions: int,
        initial_executions_used: int = 0,
    ) -> None:
        if max_executions < 1:
            raise ValueError(
                "RequestDatabaseExecutionTracker max_executions "
                "must be >= 1."
            )

        if initial_executions_used < 0:
            raise ValueError(
                "RequestDatabaseExecutionTracker "
                "initial_executions_used must be >= 0."
            )

        self._max_executions = max_executions

        # Step 25: clarification resume support - see
        # PrimarySemanticRecoveryBudget.__init__'s matching comment.
        # Clamped, never raised, and never decreased.
        self._executions_used = min(
            initial_executions_used, max_executions
        )
        self._last_execution_reason: Optional[str] = None

    @property
    def max_executions(self) -> int:
        return self._max_executions

    @property
    def executions_used(self) -> int:
        return self._executions_used

    @property
    def executions_remaining(self) -> int:
        return self._max_executions - self._executions_used

    @property
    def exhausted(self) -> bool:
        return self._executions_used >= self._max_executions

    def try_reserve(
        self,
        *,
        reason_code: Optional[str] = None,
        source_stage: Optional[str] = None,
    ) -> RequestDatabaseExecutionReservation:
        """
        Reserves exactly one execution slot IMMEDIATELY BEFORE an
        actual Oracle call is about to begin. If the tracker is
        already exhausted, `granted=False` is returned and the caller
        MUST NOT issue the Oracle call.
        """

        self._last_execution_reason = reason_code

        if self.exhausted:
            return RequestDatabaseExecutionReservation(
                granted=False,
                execution_number=None,
                reason_code=reason_code,
                source_stage=source_stage,
                state=self.snapshot(),
            )

        self._executions_used += 1

        return RequestDatabaseExecutionReservation(
            granted=True,
            execution_number=self._executions_used,
            reason_code=reason_code,
            source_stage=source_stage,
            state=self.snapshot(),
        )

    def snapshot(self) -> RequestDatabaseExecutionState:
        return RequestDatabaseExecutionState(
            max_executions=self._max_executions,
            executions_used=self._executions_used,
            executions_remaining=self.executions_remaining,
            exhausted=self.exhausted,
            last_execution_reason=self._last_execution_reason,
        )


class RequestDatabaseExecutionPolicy:
    """
    Immutable, singleton-safe policy/factory. Holds only the
    configured max_executions - never any per-request mutable state.
    Constructed once (e.g. in main.py, onto app.state) and injected
    into WorkflowManager; every logical request calls new_tracker()
    to obtain its own independent, request-scoped tracker.
    """

    def __init__(
        self,
        *,
        max_executions: Optional[int] = None,
    ) -> None:
        if max_executions is not None:
            resolved_max_executions = max_executions
        else:
            env_value = os.getenv(
                "REQUEST_DB_MAX_EXECUTIONS"
            )

            if env_value is not None:
                resolved_max_executions = int(env_value)
            else:
                resolved_max_executions = (
                    DEFAULT_REQUEST_DB_MAX_EXECUTIONS
                )

        if resolved_max_executions < 1:
            raise ValueError(
                "REQUEST_DB_MAX_EXECUTIONS must be >= 1."
            )

        self.max_executions = resolved_max_executions

    def new_tracker(self) -> RequestDatabaseExecutionTracker:
        return RequestDatabaseExecutionTracker(
            max_executions=self.max_executions
        )

    def restore_tracker(
        self, *, executions_used: int
    ) -> RequestDatabaseExecutionTracker:
        """
        Step 25: obtain a fresh, request-scoped tracker for a resumed
        request (e.g. after a persisted clarification), pre-loaded
        with the executions already used BEFORE the pause. Never
        resurrects a previous live tracker object - only restores the
        USED COUNT. Clamped to max_executions, never exceeding the
        configured ceiling.
        """

        if executions_used < 0:
            raise ValueError(
                "executions_used must be >= 0."
            )

        return RequestDatabaseExecutionTracker(
            max_executions=self.max_executions,
            initial_executions_used=executions_used,
        )
