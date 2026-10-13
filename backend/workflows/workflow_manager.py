from __future__ import annotations

from collections.abc import Callable
from agents.question_normalizer import QuestionNormalizationError, normalize_question
from typing import Any, Optional

from core.enums import ErrorCategory, WorkflowStage, WorkflowStatus
from core.models import PreparedRequest, WorkflowError, build_workflow_result
from intent.classifier import IntentClassifier, IntentClassifierError
from intent.models import IntentClassification, IntentType
from orchestration.request_db_execution_budget import RequestDatabaseExecutionPolicy
from services.feedback_context_service import FeedbackContextService, FeedbackContextServiceError
from services.feedback_rewrite_service import FeedbackRewriteService, FeedbackRewriteServiceError
from services.flagged_faq_service import FlaggedFaqService
from services.request_preprocessor import prepare_normalized_request
from services.report_variable_intent_router import has_valid_report_variables
from services.greeting_service import GreetingService, GreetingServiceError
from services.help_service import HelpService, HelpServiceError
from services.metadata_query_service import MetadataQueryService, MetadataQueryServiceError

PrimaryExecutor = Callable[..., Any]


class WorkflowManager:
    """
    Simplified application router.

    The base LLM is allowed to classify only:
      - greeting
      - data_query
      - unknown

    Help, metadata, and feedback are explicit frontend-selected request
    modes and therefore bypass the intent classifier entirely.

    There is exactly one business-data execution workflow: Primary.
    Secondary is intentionally not part of this manager.
    """

    def __init__(
        self,
        *,
        intent_classifier: IntentClassifier,
        primary_executor: PrimaryExecutor,
        greeting_service: GreetingService,
        help_service: HelpService,
        metadata_query_service: MetadataQueryService,
        feedback_context_service: FeedbackContextService,
        feedback_rewrite_service: FeedbackRewriteService,
        flagged_faq_service: Optional[FlaggedFaqService] = None,
        request_db_execution_policy: Optional[RequestDatabaseExecutionPolicy] = None,
    ) -> None:
        self.intent_classifier = intent_classifier
        self.primary_executor = primary_executor
        self.greeting_service = greeting_service
        self.help_service = help_service
        self.metadata_query_service = metadata_query_service
        self.feedback_context_service = feedback_context_service
        self.feedback_rewrite_service = feedback_rewrite_service
        self.flagged_faq_service = flagged_faq_service
        self.request_db_execution_policy = (
            request_db_execution_policy or RequestDatabaseExecutionPolicy()
        )

    @staticmethod
    def _normalize_variables(variables: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [dict(v) for v in (variables or []) if isinstance(v, dict)]

    @staticmethod
    def _routing(classification: Optional[IntentClassification], selected: str) -> dict[str, Any]:
        if classification is None:
            return {
                "classification_succeeded": None,
                "intent": None,
                "confidence": None,
                "reason": None,
                "context_used": False,
                "selected_workflow": selected,
                "primary_invoked": selected in {"primary", "feedback"},
                "secondary_invoked": False,
                "secondary_routing_enabled": False,
            }
        intent = str(getattr(classification.intent, "value", classification.intent))
        return {
            "classification_succeeded": True,
            "intent": intent,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "context_used": False,
            "selected_workflow": selected,
            "primary_invoked": selected == "primary",
            "secondary_invoked": False,
            "secondary_routing_enabled": False,
        }

    @staticmethod
    def _attach_intent(result: dict[str, Any], classification: Optional[IntentClassification]) -> dict[str, Any]:
        result = dict(result or {})
        diagnostics = dict(result.get("diagnostics") or {})
        if classification is not None:
            intent = str(getattr(classification.intent, "value", classification.intent))
            result.update(
                {
                    "intent": intent,
                    "intent_confidence": classification.confidence,
                    "intent_reason": classification.reason,
                    "intent_context_used": False,
                }
            )
        else:
            result.update(
                {
                    "intent": None,
                    "intent_confidence": None,
                    "intent_reason": None,
                    "intent_context_used": False,
                }
            )
        result["diagnostics"] = diagnostics
        result["workflow_used"] = result.get("workflow", "manager")
        return result

    async def execute(
        self,
        *,
        user_input: str,
        report_name: Optional[str],
        variables: list[dict[str, Any]],
        email: str,
        session_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        user_role: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Handle a normal_message. No conversation context is sent to the classifier."""
        cleaned_input = str(user_input or "").strip()
        normalized_variables = self._normalize_variables(variables)
        cleaned_report = str(report_name or "").strip() or None

        try:
            if has_valid_report_variables(cleaned_report, normalized_variables):
                # The user supplied a report-specific variable and its value.
                # Treat it as a data request without consulting the intent LLM.
                classification = IntentClassification(
                    intent=IntentType.DATA_QUERY,
                    confidence=1.0,
                    reason="Valid structured variable for the selected report.",
                    context_used=False,
                    is_ambiguous=False,
                    possible_intents=[],
                )
            else:
                classification = await self.intent_classifier.classify(
                    user_input=cleaned_input,
                    memory=None,
                    quoted_message=None,
                    selected_data_context=cleaned_report,
                    result_context_available=False,
                )
        except IntentClassifierError as exc:
            error = WorkflowError(
                category=ErrorCategory.INTERNAL_ERROR,
                code=exc.code,
                message="The request could not be classified.",
                retryable=True,
                details=exc.details,
            )
            return build_workflow_result(
                workflow="manager",
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.INTERNAL,
                message="The request could not be classified right now.",
                action="retry",
                user_input=cleaned_input,
                report_name=cleaned_report,
                variables=normalized_variables,
                error=error,
                diagnostics={"routing": {"classification_succeeded": False}},
            )

        intent = str(getattr(classification.intent, "value", classification.intent))

        if intent == IntentType.GREETING.value:
            try:
                message = await self.greeting_service.respond(user_input=cleaned_input)
                result = build_workflow_result(
                    workflow="greeting",
                    status=WorkflowStatus.SUCCESS,
                    stage=WorkflowStage.COMPLETED,
                    message=message,
                    action="greeting",
                    user_input=cleaned_input,
                    report_name=None,
                    variables=[],
                    diagnostics={"routing": self._routing(classification, "greeting")},
                )
            except GreetingServiceError as exc:
                result = build_workflow_result(
                    workflow="greeting",
                    status=WorkflowStatus.FAILED,
                    stage=WorkflowStage.INTERNAL,
                    message="Greeting response generation failed.",
                    action="retry",
                    user_input=cleaned_input,
                    error=WorkflowError(
                        category=ErrorCategory.INTERNAL_ERROR,
                        code=exc.code,
                        message=str(exc),
                        retryable=True,
                        details=exc.details,
                    ),
                    diagnostics={"routing": self._routing(classification, "greeting")},
                )
            return self._attach_intent(result, classification)

        if intent == IntentType.DATA_QUERY.value:
            if not cleaned_report:
                result = build_workflow_result(
                    workflow="primary",
                    status=WorkflowStatus.FAILED,
                    stage=WorkflowStage.REQUEST_VALIDATION,
                    message="A report name is required for a data query.",
                    action="report_name_required",
                    user_input=cleaned_input,
                    variables=normalized_variables,
                    error=WorkflowError(
                        category=ErrorCategory.INVALID_REQUEST,
                        code="report_name_required",
                        message="A report name is required for a data query.",
                        retryable=False,
                    ),
                    diagnostics={"routing": self._routing(classification, "primary")},
                )
                return self._attach_intent(result, classification)

            result = await self._run_primary(
                user_input=cleaned_input,
                report_name=cleaned_report,
                variables=normalized_variables,
                email=email,
                session_id=session_id,
                chat_id=chat_id,
                user_role=user_role,
            )
            diagnostics = dict(result.get("diagnostics") or {})
            diagnostics["routing"] = self._routing(classification, "primary")
            result["diagnostics"] = diagnostics
            return self._attach_intent(result, classification)

        result = build_workflow_result(
            workflow="unknown",
            status=WorkflowStatus.FAILED,
            stage=WorkflowStage.REQUEST_VALIDATION,
            message=(
                "I could not treat that as a complete standalone data request. "
                "Use Help or Metadata mode when appropriate, or reply to a specific "
                "previous result for feedback."
            ),
            action="unknown",
            user_input=cleaned_input,
            report_name=cleaned_report,
            variables=normalized_variables,
            diagnostics={"routing": self._routing(classification, "unknown")},
        )
        return self._attach_intent(result, classification)

    async def execute_help(
        self,
        *,
        user_input: str,
        report_name: Optional[str],
        variables: list[dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        cleaned_report = str(report_name or "").strip()
        if not cleaned_report:
            return self._attach_intent(
                build_workflow_result(
                    workflow="help",
                    status=WorkflowStatus.FAILED,
                    stage=WorkflowStage.REQUEST_VALIDATION,
                    message="Select a report before asking for help.",
                    action="report_name_required",
                    user_input=user_input,
                    variables=self._normalize_variables(variables),
                ),
                None,
            )
        try:
            message, matches = await self.help_service.respond(
                user_input=user_input,
                report_name=cleaned_report,
            )
            result = build_workflow_result(
                workflow="help",
                status=WorkflowStatus.SUCCESS,
                stage=WorkflowStage.COMPLETED,
                message=message,
                action="help_response",
                user_input=user_input,
                report_name=cleaned_report,
                variables=[],
                diagnostics={
                    "routing": self._routing(None, "help"),
                    "help": {"match_count": len(matches)},
                },
            )
        except HelpServiceError as exc:
            result = build_workflow_result(
                workflow="help",
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.INTERNAL,
                message="Help could not be generated right now.",
                action="retry",
                user_input=user_input,
                report_name=cleaned_report,
                error=WorkflowError(
                    category=ErrorCategory.INTERNAL_ERROR,
                    code=exc.code,
                    message=str(exc),
                    retryable=True,
                    details=exc.details,
                ),
                diagnostics={"routing": self._routing(None, "help")},
            )
        return self._attach_intent(result, None)

    async def execute_metadata(
        self,
        *,
        user_input: str,
        report_name: Optional[str],
        variables: list[dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        cleaned_report = str(report_name or "").strip()
        if not cleaned_report:
            return self._attach_intent(
                build_workflow_result(
                    workflow="metadata",
                    status=WorkflowStatus.FAILED,
                    stage=WorkflowStage.REQUEST_VALIDATION,
                    message="Select a report before asking a metadata question.",
                    action="report_name_required",
                    user_input=user_input,
                ),
                None,
            )
        try:
            service_result = await self.metadata_query_service.respond(
                user_input=user_input,
                report_name=cleaned_report,
            )
            service_status = str(service_result.get("status") or "").lower()
            success = service_status not in {"failed", "error"}
            result = build_workflow_result(
                workflow="metadata",
                status=WorkflowStatus.SUCCESS if success else WorkflowStatus.FAILED,
                stage=WorkflowStage.COMPLETED if success else WorkflowStage.REQUEST_VALIDATION,
                message=str(service_result.get("message") or "Metadata request completed."),
                action=service_result.get("action") or "metadata_response",
                user_input=user_input,
                report_name=cleaned_report,
                diagnostics={
                    "routing": self._routing(None, "metadata"),
                    "metadata": {
                        "status": service_status,
                        "match_count": len(service_result.get("matches") or []),
                    },
                },
            )
        except MetadataQueryServiceError as exc:
            result = build_workflow_result(
                workflow="metadata",
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.INTERNAL,
                message="Metadata lookup failed.",
                action="retry",
                user_input=user_input,
                report_name=cleaned_report,
                error=WorkflowError(
                    category=ErrorCategory.INTERNAL_ERROR,
                    code=exc.code,
                    message=str(exc),
                    retryable=True,
                    details=exc.details,
                ),
                diagnostics={"routing": self._routing(None, "metadata")},
            )
        return self._attach_intent(result, None)

    async def execute_feedback(
        self,
        *,
        user_input: str,
        report_name: Optional[str],
        variables: list[dict[str, Any]],
        email: str,
        chat_id: Optional[str],
        reply_context: Optional[dict[str, Any]],
        session_id: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Explicit reply-only feedback.

        Feedback no longer builds a delta JSON or reconstructs /name {value}
        syntax. It produces one new normalized business question, merges
        structured variables deterministically in Python, and enters the
        existing Primary pipeline after raw-input normalization.
        """
        normalized_variables = self._normalize_variables(variables)

        if not chat_id or not reply_context:
            return self._attach_intent(
                build_workflow_result(
                    workflow="feedback",
                    status=WorkflowStatus.FAILED,
                    stage=WorkflowStage.REQUEST_VALIDATION,
                    message="Feedback requires an explicit reply to a previous Primary result.",
                    action="reply_to_message_required",
                    user_input=user_input,
                    report_name=report_name,
                    variables=normalized_variables,
                    error=WorkflowError(
                        category=ErrorCategory.INVALID_REQUEST,
                        code="reply_to_message_required",
                        message="reply_to_message_id is required for feedback.",
                        retryable=False,
                    ),
                ),
                None,
            )

        try:
            context = await self.feedback_context_service.get_context(
                chat_id=chat_id,
                user_id=email,
                conversation_context=[],
                reply_context=reply_context,
                session_id=session_id,
            )
        except FeedbackContextServiceError as exc:
            return self._feedback_failure(
                user_input,
                report_name,
                normalized_variables,
                exc.code,
                str(exc),
                details=exc.details,
            )

        effective_report = str(
            context.report_name or ""
        ).strip()
        if not effective_report:
            return self._feedback_failure(
                user_input,
                report_name,
                normalized_variables,
                "report_name_required",
                "The replied result does not contain a usable report name.",
            )

        previous_normalized_question = str(
            context.resolved_question or ""
        ).strip()

        # Compatibility for Primary assistant messages created before
        # resolved_normalized_input started being persisted.
        if not previous_normalized_question:
            previous_original = str(
                context.original_user_input or ""
            ).strip()
            if previous_original:
                try:
                    previous_normalized_question = normalize_question(
                        previous_original,
                        context.variables,
                        require_all_input_variables=True,
                    )
                except QuestionNormalizationError:
                    previous_normalized_question = ""

        if not previous_normalized_question:
            return self._feedback_failure(
                user_input,
                effective_report,
                normalized_variables,
                "previous_normalized_question_required",
                (
                    "The replied result does not contain enough normalized "
                    "request context. Please run the original query again once."
                ),
            )

        try:
            normalized_feedback = normalize_question(
                user_input,
                normalized_variables,
                require_all_input_variables=True,
            )
        except QuestionNormalizationError as exc:
            return self._feedback_failure(
                user_input,
                effective_report,
                normalized_variables,
                exc.code,
                str(exc),
                action="fix_variable_syntax",
                details=exc.details,
            )

        merged_variables = self._merge_feedback_variables(
            context.variables,
            normalized_variables,
        )

        previous_variable_names = self._input_variable_names(
            context.variables
        )
        current_variable_names = self._input_variable_names(
            normalized_variables
        )

        try:
            rewritten_normalized_question = (
                await self.feedback_rewrite_service.rewrite(
                    previous_normalized_question=(
                        previous_normalized_question
                    ),
                    normalized_feedback=normalized_feedback,
                    previous_variable_names=(
                        previous_variable_names
                    ),
                    current_variable_names=(
                        current_variable_names
                    ),
                )
            )
        except FeedbackRewriteServiceError as exc:
            return self._feedback_failure(
                user_input,
                effective_report,
                normalized_variables,
                exc.code,
                str(exc),
                retryable=True,
                details=exc.details,
            )

        prepared_request = prepare_normalized_request(
            normalized_input=rewritten_normalized_question,
            report_name=effective_report,
            variables=merged_variables,
        )

        result = await self._run_primary(
            user_input=rewritten_normalized_question,
            report_name=effective_report,
            variables=prepared_request.normalized_variables,
            email=email,
            session_id=session_id,
            chat_id=chat_id,
            user_role=user_role,
            prepared_request=prepared_request,
        )

        result["workflow_used"] = "primary"
        result["request_mode"] = "feedback"
        result["original_feedback_input"] = user_input
        result["normalized_feedback_input"] = normalized_feedback
        result["resolved_user_input"] = rewritten_normalized_question
        result["resolved_normalized_input"] = rewritten_normalized_question

        diagnostics = dict(result.get("diagnostics") or {})
        diagnostics["routing"] = self._routing(None, "feedback")
        diagnostics["feedback_reexecution"] = {
            "decision_route": "normalized_question_to_primary",
            "selection_source": getattr(
                context,
                "selection_source",
                None,
            ),
            "original_user_question": str(
                context.original_user_input or ""
            ).strip(),
            "feedback_input": user_input,
            "normalized_feedback_input": normalized_feedback,
            "previous_message_id": getattr(
                context,
                "message_id",
                None,
            ),
            "previous_workflow_id": getattr(
                context,
                "workflow_id",
                None,
            ),
            "effective_report_name": effective_report,
            "previous_normalized_question": (
                previous_normalized_question
            ),
            "rewritten_normalized_question": (
                rewritten_normalized_question
            ),
            "previous_variable_names": (
                previous_variable_names
            ),
            "current_feedback_variable_names": (
                current_variable_names
            ),
            "effective_variable_names": (
                prepared_request.variable_names
            ),
            "variables_key": prepared_request.variables_key,
        }

        if (
            self.flagged_faq_service is not None
            and bool(result.get("query_executed"))
            and str(result.get("status") or "").lower()
            == WorkflowStatus.SUCCESS.value
        ):
            try:
                diagnostics["flagged_faq"] = (
                    await self.flagged_faq_service.record_feedback_candidate(
                        report_name=effective_report,
                        original_user_question=str(
                            context.original_user_input or ""
                        ).strip(),
                        previous_normalized_question=(
                            previous_normalized_question
                        ),
                        feedback_input=user_input,
                        normalized_feedback=normalized_feedback,
                        new_normalized_question=(
                            rewritten_normalized_question
                        ),
                        variables=(
                            prepared_request.normalized_variables
                        ),
                        variable_names=(
                            prepared_request.variable_names
                        ),
                        variables_key=(
                            prepared_request.variables_key
                        ),
                        sql_template=result.get("sql_template"),
                        executed_sql=result.get("sql"),
                        result_count=int(
                            result.get("result_count") or 0
                        ),
                        chat_id=chat_id,
                        source_assistant_message_id=getattr(
                            context,
                            "message_id",
                            None,
                        ),
                        workflow_id=result.get("workflow_id"),
                    )
                )
            except Exception as exc:
                # The review queue is best-effort. A successful user query
                # must never fail because Excel could not be written.
                diagnostics["flagged_faq"] = {
                    "stored": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }

        result["diagnostics"] = diagnostics
        result.update(
            {
                "intent": None,
                "intent_confidence": None,
                "intent_reason": None,
                "intent_context_used": False,
            }
        )
        return result

    @staticmethod
    def _input_variable_names(
        variables: list[dict[str, Any]] | None,
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for variable in variables or []:
            if not isinstance(variable, dict):
                continue
            source = str(
                variable.get("source") or "input"
            ).strip().lower()
            if source == "option":
                continue
            name = str(
                variable.get("name") or ""
            ).strip().lower()
            if not name or name in seen:
                continue
            names.append(name)
            seen.add(name)

        return names

    @staticmethod
    def _merge_feedback_variables(
        previous_variables: list[dict[str, Any]] | None,
        current_variables: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """
        Keep replied-query variables unless feedback explicitly supplies the
        same variable again. Current feedback values replace previous values
        for the same (source, name). Repeated current values are preserved.

        This intentionally does not ask the LLM to emit variable names/values.
        """

        def key(variable: dict[str, Any]) -> tuple[str, str]:
            source = str(
                variable.get("source") or "input"
            ).strip().lower()
            if source != "option":
                source = "input"
            name = str(
                variable.get("name") or ""
            ).strip().lower()
            return source, name

        cleaned_current = [
            dict(variable)
            for variable in (current_variables or [])
            if isinstance(variable, dict)
            and str(variable.get("name") or "").strip()
        ]

        overridden_keys = {
            key(variable)
            for variable in cleaned_current
        }

        merged = [
            dict(variable)
            for variable in (previous_variables or [])
            if isinstance(variable, dict)
            and str(variable.get("name") or "").strip()
            and key(variable) not in overridden_keys
        ]
        merged.extend(cleaned_current)
        return merged

    def _feedback_failure(
        self,
        user_input: str,
        report_name: Optional[str],
        variables: list[dict[str, Any]],
        code: str,
        message: str,
        retryable: bool = False,
        action: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        result = build_workflow_result(
            workflow="feedback",
            status=WorkflowStatus.FAILED,
            stage=WorkflowStage.REQUEST_VALIDATION,
            message=message,
            action=(
                action
                or ("retry" if retryable else "clarification_required")
            ),
            user_input=user_input,
            report_name=report_name,
            variables=variables,
            error=WorkflowError(
                category=(
                    ErrorCategory.INTERNAL_ERROR
                    if retryable
                    else ErrorCategory.INVALID_REQUEST
                ),
                code=code,
                message=message,
                retryable=retryable,
                details=details or {},
            ),
            diagnostics={"routing": self._routing(None, "feedback")},
        )
        return self._attach_intent(result, None)

    async def _run_primary(
        self,
        *,
        user_input: str,
        report_name: str,
        variables: list[dict[str, Any]],
        email: str,
        session_id: Optional[str],
        chat_id: Optional[str],
        user_role: Optional[str],
        prepared_request: Optional[PreparedRequest] = None,
    ) -> dict[str, Any]:
        tracker = self.request_db_execution_policy.new_tracker()
        result = await self.primary_executor(
            user_input=user_input,
            report_name=report_name,
            variables=variables,
            email=email,
            session_id=session_id,
            chat_id=chat_id,
            user_role=user_role,
            request_db_execution_tracker=tracker,
            prepared_request=prepared_request,
        )
        result = dict(result or {})
        diagnostics = dict(result.get("diagnostics") or {})
        diagnostics["request_db_execution"] = tracker.snapshot().model_dump(mode="json")
        result["diagnostics"] = diagnostics
        result["workflow_used"] = result.get("workflow", "primary")
        return result
