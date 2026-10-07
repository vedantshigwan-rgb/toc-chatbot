from __future__ import annotations

from typing import Any, Optional

from core.models import FeedbackExecutionContext


# Workflow types that produce a reusable, structured business
# execution. Greeting/general/help/unknown never do. metadata_query
# is deliberately excluded: it produces an explanation, not
# modifiable business data, and result_analysis is excluded too since
# it never stores its own new SQL/rows distinct from what it read.
#
# "primary"/"value_lookup" are the exact `workflow_used` values
# already written by WorkflowManager._attach_manager_intent (which
# sets workflow_used to whatever the branch's own `workflow` field
# was). "secondary" is the exact `workflow_used` value the
# /secondary/prepare route persists (Step 35 persistence acceptance
# fix) - it produces the SAME persisted message shape (sql,
# table_rows, result_count, status, stage), so it is equally reusable
# here. This does NOT enable automatic Primary -> Secondary routing
# (that remains Step 36's concern) - it only lets feedback reconstruct
# a Secondary execution that was already directly, explicitly
# requested and persisted.
REUSABLE_WORKFLOW_TYPES = {"primary"}


class FeedbackContextServiceError(RuntimeError):
    """
    Raised when a previous execution context cannot be located or
    reconstructed safely. This is a controlled outcome, not an
    internal failure - WorkflowManager maps it to
    clarification_required.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:

        super().__init__(message)

        self.code = code
        self.details = details or {}


def _is_reusable_execution_context(
    message: Optional[dict[str, Any]],
) -> bool:
    """
    Deterministic predicate: does a stored message contain enough
    state to be meaningfully modified by a feedback follow-up?

    Only assistant messages produced by a reusable workflow type
    qualify. Greetings, general questions, help answers, and
    metadata-only explanations never do, even if they are the most
    recent message in the chat.
    """

    if not message:
        return False

    role = str(message.get("role") or "").strip().lower()

    if role != "assistant":
        return False

    workflow_used = str(
        message.get("workflow_used") or ""
    ).strip()

    return workflow_used in REUSABLE_WORKFLOW_TYPES


class FeedbackContextService:
    """
    Reconstructs the previous execution context a feedback message
    should be understood against.

    This service does NOT:
    - apply the feedback modification
    - generate a delta
    - call an LLM
    - execute SQL
    - call Primary or Secondary

    Selection is entirely deterministic, using conversation/message
    identifiers, reply references, and stored workflow type - never
    semantic interpretation.
    """

    def __init__(
        self,
        *,
        chat_store: Any,
    ) -> None:

        self.chat_store = chat_store

    # ========================================================
    # PUBLIC
    # ========================================================

    async def get_context(
        self,
        *,
        chat_id: Optional[str],
        user_id: str,
        conversation_context: list[dict[str, Any]],
        reply_context: Optional[dict[str, Any]],
        session_id: Optional[str],
    ) -> FeedbackExecutionContext:
        """
        Locate and reconstruct the most relevant previous executable
        context for a feedback message.

        `reply_context` must identify the exact assistant Primary result
        selected by the user. conversation_context is ignored by design.
        """

        selected_message: Optional[dict[str, Any]] = None
        selection_source: Optional[str] = None

        # ====================================================
        # PRECEDENCE 1: EXPLICIT REPLY/REFERENCE
        # ====================================================

        if reply_context:

            target = reply_context.get("target_message")

            if _is_reusable_execution_context(target):

                selected_message = target
                selection_source = "explicit_reply"

        # Explicit reply is mandatory. There is deliberately no
        # "latest reusable execution" fallback: feedback must always
        # target the exact assistant result selected by the user.

        # ====================================================
        # NO USABLE PREVIOUS CONTEXT
        # ====================================================

        if selected_message is None:

            raise FeedbackContextServiceError(
                "No usable previous execution was found "
                "for this feedback request.",
                code="feedback_context_required",
            )

        # ====================================================
        # CONVERSATION ISOLATION
        # ====================================================
        #
        # Defensive check even though callers already scope
        # conversation_context/reply_context by chat_id and user_id.

        if (
            chat_id
            and selected_message.get("chat_id")
            and selected_message.get("chat_id") != chat_id
        ):

            raise FeedbackContextServiceError(
                "The selected previous execution belongs "
                "to a different conversation.",
                code="feedback_context_isolation_violation",
            )

        # ====================================================
        # LOAD THE FULL STORED DOCUMENT
        # ====================================================
        #
        # conversation_context entries are a restricted projection
        # (no sql/table_rows/workflow_id/result_count). The
        # explicit-reply target already comes from get_reply_context,
        # which returns the full document, so no extra read is
        # needed there.

        full_message = selected_message

        if selection_source == "latest_reusable":

            message_id = str(
                selected_message.get("message_id") or ""
            ).strip()

            if message_id:

                fetched = (
                    await self.chat_store.get_message(
                        message_id=message_id,
                        user_id=user_id,
                    )
                )

                if fetched is not None:
                    full_message = fetched

        return await self._build_context(
            message=full_message,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
            selection_source=selection_source,
        )

    # ========================================================
    # SOURCE USER MESSAGE
    # ========================================================

    async def _load_source_user_message(
        self,
        *,
        assistant_message: dict[str, Any],
        chat_id: Optional[str],
        user_id: str,
    ) -> Optional[dict[str, Any]]:
        """
        Retrieve the full stored user message that produced the
        selected assistant execution, via its stored
        source_user_message_id.

        This never raises: a missing/absent reference, a message
        that no longer exists, or any lookup failure all degrade to
        None (historical context degradation, not a crash). Shared
        by original-user-input reconstruction and the legacy
        variable-fallback path (Step 35 variable context fix) so
        only one extra Mongo read is performed per request.
        """

        source_user_message_id = str(
            assistant_message.get(
                "source_user_message_id"
            )
            or ""
        ).strip()

        if not source_user_message_id:
            return None

        try:

            source_message = (
                await self.chat_store.get_message(
                    message_id=source_user_message_id,
                    user_id=user_id,
                )
            )

        except Exception:
            return None

        if not source_message:
            return None

        # ====================================================
        # CONVERSATION ISOLATION
        # ====================================================
        #
        # MongoChatStore.get_message() scopes only by
        # (message_id, user_id), not chat_id. Validate the
        # returned document belongs to the same conversation as
        # the assistant execution it is meant to explain before
        # trusting its content.

        expected_chat_id = (
            assistant_message.get("chat_id")
            or chat_id
        )

        if (
            expected_chat_id
            and source_message.get("chat_id")
            and source_message.get("chat_id")
            != expected_chat_id
        ):
            return None

        return source_message

    # ========================================================
    # RECONSTRUCTION
    # ========================================================

    async def _build_context(
        self,
        *,
        message: dict[str, Any],
        chat_id: Optional[str],
        user_id: str,
        session_id: Optional[str],
        selection_source: Optional[str],
    ) -> FeedbackExecutionContext:

        # The selected message is always the assistant execution
        # itself (see _is_reusable_execution_context), so the
        # original user request/variables are retrieved via its
        # stored source_user_message_id - one shared read serves
        # both original_user_input and the legacy variable fallback
        # below.
        source_message = (
            await self._load_source_user_message(
                assistant_message=message,
                chat_id=chat_id,
                user_id=user_id,
            )
        )

        # ====================================================
        # VARIABLES
        # ====================================================
        #
        # The assistant execution's own persisted variables are
        # authoritative when present (Step 35 variable context
        # fix persists them going forward). An older assistant
        # record persisted before that fix has no `variables` of
        # its own - fall back to its linked source user message's
        # variables, the same request variables that produced this
        # execution.

        variables = list(
            message.get("variables") or []
        )

        if not variables and source_message:
            variables = list(
                source_message.get("variables") or []
            )

        extra_variables = [
            variable
            for variable in variables
            if str(
                variable.get("source") or ""
            ).strip().lower()
            == "option"
        ]

        result_rows = list(
            message.get("table_rows") or []
        )

        result_columns = (
            list(result_rows[0].keys())
            if result_rows
            else []
        )

        created_at = message.get("created_at")

        resolved_user_input = str(
            message.get("resolved_user_input") or ""
        ).strip()

        original_user_input = (
            resolved_user_input
            or (
                str(source_message.get("content"))
                if source_message
                and source_message.get("content") is not None
                else None
            )
        )

        resolved_question = str(
            message.get("resolved_normalized_input")
            or (
                source_message.get("normalized_input")
                if source_message
                else ""
            )
            or ""
        ).strip() or None

        return FeedbackExecutionContext(
            chat_id=(
                message.get("chat_id") or chat_id
            ),
            message_id=message.get("message_id"),
            source_user_message_id=(
                message.get(
                    "source_user_message_id"
                )
            ),
            workflow_id=message.get("workflow_id"),
            session_id=session_id,

            original_user_input=original_user_input,
            resolved_question=resolved_question,

            workflow=message.get("workflow_used"),
            report_name=message.get("report_name"),

            variables=variables,
            extra_variables=extra_variables,

            sql=message.get("sql"),

            result_columns=result_columns,
            result_rows=result_rows,
            row_count=int(
                message.get("result_count") or 0
            ),

            previous_assistant_message=(
                message.get("content")
                if str(
                    message.get("role") or ""
                ).strip().lower()
                == "assistant"
                else None
            ),

            status=message.get("status"),
            stage=message.get("stage"),

            created_at=(
                str(created_at)
                if created_at is not None
                else None
            ),

            selection_source=selection_source,
        )
