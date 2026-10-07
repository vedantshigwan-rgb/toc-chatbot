from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, ConfigDict

# Step 20: current architecture default. See this module's own
# docstrings for exactly what does/does not consume an attempt.
DEFAULT_PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS = 2


class PrimarySemanticRecoveryBudgetState(BaseModel):
    """
    Typed, JSON-safe snapshot of one request-scoped semantic-recovery
    budget at a point in time. Safe for model_dump(mode="json") -
    never carries SQL text, Oracle errors, prompts, or other private
    data (Step 20 Step 7/22).
    """

    model_config = ConfigDict(extra="forbid")

    max_attempts: int
    attempts_used: int = 0
    attempts_remaining: int
    exhausted: bool = False
    last_reason_code: Optional[str] = None


class PrimarySemanticRecoveryReservation(BaseModel):
    """
    Result of one try_reserve() call. `granted=False` means the
    budget was already exhausted and NO attempt was consumed -
    `state` is unchanged from immediately before the call.
    """

    model_config = ConfigDict(extra="forbid")

    granted: bool
    attempt_number: Optional[int] = None
    reason_code: Optional[str] = None
    source_stage: Optional[str] = None
    state: PrimarySemanticRecoveryBudgetState


class PrimarySemanticRecoveryBudget:
    """
    Step 20: mutable, REQUEST-SCOPED tracker for how many additional
    Primary SEMANTIC recovery attempts remain after the initial
    Primary execution.

    This is a plain Python object - it must be constructed fresh per
    logical request (via PrimarySemanticRecoveryPolicy.new_budget())
    and held only as a local variable for the lifetime of that
    request. It must NEVER be stored on app.state, a WorkflowManager
    instance, a module-level global, or any other object shared
    across concurrent requests - doing so would let one user's
    semantic-recovery attempts silently consume another user's
    budget.

    A PRIMARY SEMANTIC RECOVERY ATTEMPT is an additional Primary
    attempt performed because the backend is trying to semantically
    correct/rebuild/adapt the query/request after the initial Primary
    attempt (e.g. regenerating SQL after a recoverable failure,
    retrying Primary after a recoverable adequacy outcome). It is
    NOT: the initial Primary execution, a same-SQL transient Oracle
    retry, a semantic checker retry, ResultCompletenessEvaluator,
    ZeroRowsEvaluator, value-validation diagnostics, clarification
    creation/resume, metadata lookup, ResultAnalysis, Secondary
    execution, or the mere creation of an ErrorStrategyResolver
    RecoveryDecision. None of those call try_reserve().

    Step 20 wires this budget's existence/diagnostics only - nothing
    in the current codebase calls try_reserve() yet, so attempts_used
    remains 0 for every real request until a future step (22+) wires
    an actual semantic recovery action to reserve against it.
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        initial_attempts_used: int = 0,
    ) -> None:
        if max_attempts < 0:
            raise ValueError(
                "PrimarySemanticRecoveryBudget max_attempts must "
                "be >= 0."
            )

        if initial_attempts_used < 0:
            raise ValueError(
                "PrimarySemanticRecoveryBudget "
                "initial_attempts_used must be >= 0."
            )

        self._max_attempts = max_attempts

        # Step 25: clarification resume support. A restored budget
        # must never exceed its own ceiling and must never decrease an
        # already-used count - clamped, not raised, so a request
        # resuming after a persisted clarification with
        # attempts_used=2 against a max_attempts=2 budget correctly
        # starts already-exhausted rather than erroring.
        self._attempts_used = min(
            initial_attempts_used, max_attempts
        )
        self._last_reason_code: Optional[str] = None

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def attempts_used(self) -> int:
        return self._attempts_used

    @property
    def attempts_remaining(self) -> int:
        return self._max_attempts - self._attempts_used

    @property
    def exhausted(self) -> bool:
        return self._attempts_used >= self._max_attempts

    def try_reserve(
        self,
        *,
        reason_code: Optional[str] = None,
        source_stage: Optional[str] = None,
    ) -> PrimarySemanticRecoveryReservation:
        """
        Reserves exactly one attempt IMMEDIATELY BEFORE an actual
        semantic recovery attempt is about to begin - never merely
        because a recoverable condition was detected or a
        RecoveryDecision was created. Once reserved, the attempt
        remains consumed even if the subsequent recovery attempt
        itself later fails (generation/validation/execution/checker
        rejection) - reservations are never refunded, to guarantee
        the budget is a hard, monotonic cap.
        """

        self._last_reason_code = reason_code

        if self.exhausted:
            return PrimarySemanticRecoveryReservation(
                granted=False,
                attempt_number=None,
                reason_code=reason_code,
                source_stage=source_stage,
                state=self.snapshot(),
            )

        self._attempts_used += 1

        return PrimarySemanticRecoveryReservation(
            granted=True,
            attempt_number=self._attempts_used,
            reason_code=reason_code,
            source_stage=source_stage,
            state=self.snapshot(),
        )

    def snapshot(self) -> PrimarySemanticRecoveryBudgetState:
        return PrimarySemanticRecoveryBudgetState(
            max_attempts=self._max_attempts,
            attempts_used=self._attempts_used,
            attempts_remaining=self.attempts_remaining,
            exhausted=self.exhausted,
            last_reason_code=self._last_reason_code,
        )


class PrimarySemanticRecoveryPolicy:
    """
    Immutable, singleton-safe policy/factory. Holds only the
    configured max_attempts - never any per-request mutable state.
    Constructed once (e.g. in main.py, onto app.state) and injected
    into WorkflowManager; every logical request calls new_budget()
    to obtain its own independent, request-scoped budget.
    """

    def __init__(self, *, max_attempts: Optional[int] = None) -> None:
        if max_attempts is not None:
            resolved_max_attempts = max_attempts
        else:
            resolved_max_attempts = int(
                os.getenv(
                    "PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS",
                    str(
                        DEFAULT_PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS
                    ),
                )
            )

        if resolved_max_attempts < 0:
            raise ValueError(
                "PRIMARY_SEMANTIC_RECOVERY_MAX_ATTEMPTS must be "
                ">= 0."
            )

        self.max_attempts = resolved_max_attempts

    def new_budget(self) -> PrimarySemanticRecoveryBudget:
        return PrimarySemanticRecoveryBudget(
            max_attempts=self.max_attempts
        )

    def restore_budget(
        self, *, attempts_used: int
    ) -> PrimarySemanticRecoveryBudget:
        """
        Step 25: obtain a fresh, request-scoped budget object for a
        resumed request (e.g. after a persisted clarification), pre-
        loaded with the attempts already used BEFORE the pause. This
        never resurrects a previous live budget object (those are
        never persisted - see the class docstring) - it only restores
        the USED COUNT so the resumed request cannot silently regain
        attempts it already spent. attempts_used is clamped to
        max_attempts, never allowed to exceed the configured ceiling.
        """

        if attempts_used < 0:
            raise ValueError(
                "attempts_used must be >= 0."
            )

        return PrimarySemanticRecoveryBudget(
            max_attempts=self.max_attempts,
            initial_attempts_used=attempts_used,
        )
