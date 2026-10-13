from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
import re

from core.enums import (
    ErrorCategory,
    WorkflowStage,
    WorkflowStatus,
)
from core.models import (
    PreparedRequest,
    WorkflowError,
    build_workflow_result,
)
from services.oracle_schema_validator import (
    validate_sql_against_oracle_schema,
)
from services.faq_service import (
    prepare_faq_examples,
    search_faqs,
)
from services.primary_sql_generation_service import (
    generate_primary_sql,
)
from services.request_preprocessor import (
    prepare_request,
)
from agents.question_normalizer import QuestionNormalizationError
from services.primary_execution_retry_service  import (
    DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS,
    execute_primary_sql_with_retry,
)
from orchestration.request_db_execution_budget import (
    RequestDatabaseExecutionTracker,
)
from services.sql_injection_service import (
    inject_sql_values,
)
from services.workflow_storage_service import (
    create_primary_workflow_record,
    safe_store_generation_error,
    safe_store_generation_result,
)
from services.variable_binding_resolver import (
    resolve_variable_bindings,
    validate_bindings_with_mapping_fallback,
)
from services.value_validator import (
    validate_bound_values,
)
# Step 24 Validation Fix: this system's own Oracle-semantic-recovery
# eligibility set, owned here (not primary_sql_repair_service's
# SUPPORTED_REPAIR_CATEGORIES, which describes the now-unused legacy
# repair service's own, narrower eligibility). Must match
# ErrorStrategyResolver._SQL_REPAIR_CATEGORIES exactly - both describe
# the same policy decision from two different vantage points (evidence
# gathering here, actual routing there).
ORACLE_SQL_SHAPE_RECOVERY_CATEGORIES = {
    "sql_syntax_error",
    "invalid_grouping",
    "bind_error",
}
from services.result_analysis_service import (
    generate_result_analysis,
)
from services.extra_sql_filter_service import (
    apply_extra_sql_filters,
)
from services.report_option_sql_filter_service import (
    apply_report_option_sql_filters,
)
from utils.openai_pool import OpenAIPool
from utils.search_faiss_indexes import (
    FAQIndexSearcher,
)
from diagnostics.oracle_error_classifier import (
    classify_oracle_error,
)
from validation.sql_validator import (
    validate_sql,
)



def _make_error(
    *,
    category: ErrorCategory,
    message: str,
    code: Optional[str] = None,
    retryable: Optional[bool] = None,
    details: Optional[Dict[str, Any]] = None,
) -> WorkflowError:
    return WorkflowError(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    )





SQL_PLACEHOLDER_PATTERN = re.compile(
    r"\{[A-Za-z_][A-Za-z0-9_]*\}"
)


ORACLE_INVALID_IDENTIFIER_PATTERN = re.compile(
    r"ORA-00904:\s*"
    r"(?:\"([^\"]+)\"|([A-Za-z0-9_$#.]+))",
    flags=re.IGNORECASE,
)


def _extract_trusted_allowed_tables(
    prepared_results: List[Dict[str, Any]],
) -> List[str]:
    """
    Extract physical Oracle table names from trusted FAQ SQL
    templates returned by the report-specific FAISS index.

    Source:
        prepared_results[i]["sql_template"]

    This does not use raw user values, Redis, YAML, or Weaviate.
    """

    allowed_tables: set[str] = set()

    for example in prepared_results or []:
        if not isinstance(example, dict):
            continue

        # Use the original FAQ SQL template, not the SQL containing
        # injected user values.
        trusted_sql_template = str(
            example.get(
                "sql_template",
                "",
            )
            or ""
        ).strip()

        if not trusted_sql_template:
            continue

        # Make placeholders parseable without inserting real values.
        #
        # Examples:
        #   LIKE '%{buyer_name}%'
        #       -> LIKE '%TOC_VALUE%'
        #
        #   ITEM = {item}
        #       -> ITEM = TOC_VALUE
        parseable_sql = (
            SQL_PLACEHOLDER_PATTERN.sub(
                "TOC_VALUE",
                trusted_sql_template,
            )
        )

        validation_result = validate_sql(
            sql=parseable_sql,
            dialect="oracle",
            allowed_tables=None,
        )

        # Do not trust tables extracted from malformed or unsafe FAQ SQL.
        if not validation_result.valid:
            continue

        for table_reference in (
            validation_result.tables
        ):
            table_name = str(
                table_reference.qualified_name
                or table_reference.name
                or ""
            ).strip()

            if table_name:
                allowed_tables.add(
                    table_name
                )

    return sorted(
        allowed_tables,
        key=str.upper,
    )





def _base_table_name(
    table_name: str,
) -> str:
    """
    Convert a schema-qualified Oracle table name into
    the base table name used by Weaviate metadata.

    Example:
        KPCL_REPORTING.KPC_FULL_KIT_INV_SUMMARY
        -> KPC_FULL_KIT_INV_SUMMARY
    """

    cleaned_table_name = str(
        table_name or ""
    ).strip()

    if not cleaned_table_name:
        return ""

    return (
        cleaned_table_name
        .split(".")[-1]
        .strip()
        .strip('"')
    )


def _extract_invalid_identifier(
    oracle_error_message: Optional[str],
) -> Optional[str]:
    """
    Extract the identifier reported by ORA-00904.
    """

    message = str(
        oracle_error_message or ""
    ).strip()

    if not message:
        return None

    match = (
        ORACLE_INVALID_IDENTIFIER_PATTERN
        .search(message)
    )

    if not match:
        return None

    identifier = (
        match.group(1)
        or match.group(2)
        or ""
    ).strip()

    return identifier or None




async def _validate_primary_metadata_error(
    *,
    metadata_repository,
    oracle_classification,
    oracle_error_code: Optional[str],
    oracle_error_message: Optional[str],
    allowed_tables: List[str],
) -> Dict[str, Any]:
    """
    Verify invalid-column and invalid-table Oracle errors
    using the existing Weaviate metadata repository.

    This function performs verification only.
    It does not alter or repair SQL.
    """

    category = str(
        getattr(
            oracle_classification,
            "category",
            "",
        )
        or ""
    ).strip()

    diagnostics: Dict[str, Any] = {
        "status": "not_run",
        "category": category,
        "oracle_error_code": (
            oracle_error_code
        ),
        "invalid_identifier": None,
        "metadata_repository_available": (
            metadata_repository
            is not None
        ),
        "table_lookups": [],
        "available_columns": [],
        "exact_column_matches": [],
    }

    if metadata_repository is None:
        diagnostics.update(
            {
                "status": (
                    "metadata_repository_unavailable"
                ),
                "message": (
                    "The metadata repository is "
                    "not available."
                ),
            }
        )

        return diagnostics

    metadata_records = []

    for allowed_table in (
        allowed_tables or []
    ):
        base_table_name = (
            _base_table_name(
                allowed_table
            )
        )

        if not base_table_name:
            continue

        try:
            lookup_result = (
                await metadata_repository
                .get_table_columns_any_module(
                    table_name=(
                        base_table_name
                    ),
                    limit=500,
                )
            )

            diagnostics[
                "table_lookups"
            ].append(
                {
                    "allowed_table": (
                        allowed_table
                    ),
                    "base_table_name": (
                        base_table_name
                    ),
                    "result": (
                        lookup_result.model_dump(
                            mode="json"
                        )
                    ),
                }
            )

            if (
                lookup_result.success
                and lookup_result.found
            ):
                metadata_records.extend(
                    lookup_result.records
                )

        except Exception as error:
            diagnostics[
                "table_lookups"
            ].append(
                {
                    "allowed_table": (
                        allowed_table
                    ),
                    "base_table_name": (
                        base_table_name
                    ),
                    "result": {
                        "success": False,
                        "found": False,
                        "error_code": (
                            "metadata_lookup_exception"
                        ),
                        "message": str(error),
                        "exception_type": (
                            type(error).__name__
                        ),
                    },
                }
            )

    available_columns = []

    seen_columns = set()

    for record in metadata_records:
        column_key = (
            record.module.casefold(),
            record.table_name.casefold(),
            record.column_name.casefold(),
        )

        if column_key in seen_columns:
            continue

        seen_columns.add(column_key)

        available_columns.append(
            {
                "module": record.module,
                "table_name": (
                    record.table_name
                ),
                "column_name": (
                    record.column_name
                ),
                "data_type": (
                    record.data_type
                ),
                "description": (
                    record.description
                ),
            }
        )

    diagnostics[
        "available_columns"
    ] = available_columns

    if category == "invalid_column":
        invalid_identifier = (
            _extract_invalid_identifier(
                oracle_error_message
            )
        )

        diagnostics[
            "invalid_identifier"
        ] = invalid_identifier

        if not invalid_identifier:
            diagnostics.update(
                {
                    "status": (
                        "identifier_not_extracted"
                    ),
                    "message": (
                        "Oracle reported an invalid "
                        "identifier, but its name "
                        "could not be extracted."
                    ),
                }
            )

            return diagnostics

        exact_matches = [
            column
            for column in available_columns
            if (
                str(
                    column.get(
                        "column_name",
                        "",
                    )
                ).strip().casefold()
                == invalid_identifier.casefold()
            )
        ]

        diagnostics[
            "exact_column_matches"
        ] = exact_matches

        if exact_matches:
            diagnostics.update(
                {
                    "status": (
                        "identifier_exists_in_metadata"
                    ),
                    "metadata_confirmed_invalid": (
                        False
                    ),
                    "message": (
                        "The identifier exists in "
                        "metadata. The Oracle error "
                        "may be caused by aliasing, "
                        "qualification, scope, or "
                        "database accessibility."
                    ),
                }
            )

        else:
            diagnostics.update(
                {
                    "status": (
                        "invalid_column_confirmed"
                    ),
                    "metadata_confirmed_invalid": (
                        True
                    ),
                    "message": (
                        "The Oracle identifier was "
                        "not found in the trusted "
                        "table metadata."
                    ),
                }
            )

        return diagnostics

    if category == "invalid_table_or_view":
        found_tables = sorted(
            {
                str(
                    column.get(
                        "table_name",
                        "",
                    )
                ).strip()
                for column in available_columns
                if str(
                    column.get(
                        "table_name",
                        "",
                    )
                ).strip()
            },
            key=str.upper,
        )

        diagnostics[
            "found_tables"
        ] = found_tables

        if found_tables:
            diagnostics.update(
                {
                    "status": (
                        "table_exists_in_metadata"
                    ),
                    "metadata_confirmed_invalid": (
                        False
                    ),
                    "message": (
                        "The table exists in "
                        "metadata. ORA-00942 may be "
                        "caused by schema, synonym, "
                        "or permission configuration."
                    ),
                }
            )

        else:
            diagnostics.update(
                {
                    "status": (
                        "invalid_table_confirmed"
                    ),
                    "metadata_confirmed_invalid": (
                        True
                    ),
                    "message": (
                        "None of the trusted SQL "
                        "tables were found in "
                        "Weaviate metadata."
                    ),
                }
            )

        return diagnostics

    diagnostics.update(
        {
            "status": (
                "unsupported_metadata_category"
            ),
            "message": (
                "The Oracle error category does "
                "not require metadata validation."
            ),
        }
    )

    return diagnostics






class PrimaryWorkflow:
    """
    Executes the complete FAQ-based primary SQL workflow.

    Dependencies are provided through the constructor so the
    future WorkflowManager can invoke this workflow directly.
    """

    def __init__(
        self,
        *,
        pool: OpenAIPool,
        faq_searcher: FAQIndexSearcher,
        workflow_store,
        metadata_repository=None,
        oracle_schema_validator=(
            validate_sql_against_oracle_schema
        ),
    ) -> None:
        self.pool = pool
        self.faq_searcher = faq_searcher
        self.workflow_store = workflow_store

        self.metadata_repository = (
            metadata_repository
        )

        self.oracle_schema_validator = (
            oracle_schema_validator
        )
    async def execute(
        self,
        *,
        user_input: str,
        report_name: str,
        variables: list,
        email: str,
        session_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        user_role: Optional[str] = None,
        request_db_execution_tracker: Optional[
            RequestDatabaseExecutionTracker
        ] = None,
        recovery_hint: Optional[str] = None,
        prepared_request: Optional[PreparedRequest] = None,
    ) -> dict:
        """
        Public workflow entry point.

        Unexpected exceptions are converted into the common
        WorkflowResult contract.

        `request_db_execution_tracker` is optional (Step 21). When
        supplied by the caller (WorkflowManager, at the logical
        Primary request boundary), it is passed through to every
        actual-Oracle-executing step of this workflow - business SQL
        execution/retry, Oracle schema validation, and zero-row value
        validation - so all of them share one request-level DB
        execution safety accounting. It is never used to decide
        semantic recovery; that remains the separate, unrelated
        PrimarySemanticRecoveryBudget owned by WorkflowManager.

        `recovery_hint` is optional (Step 23). When supplied by
        WorkflowManager's bounded static semantic recovery loop, it is
        passed straight through to SQL generation's LLM prompt only -
        it never changes FAQ search/retrieval (query_without_values is
        computed independently, before generation runs) and never
        alters SQL validation/execution behavior.
        """

        try:
            return await self._execute(
                user_input=user_input,
                report_name=report_name,
                variables=variables,
                email=email,
                session_id=session_id,
                chat_id=chat_id,
                user_role=user_role,
                request_db_execution_tracker=(
                    request_db_execution_tracker
                ),
                recovery_hint=recovery_hint,
                prepared_request=prepared_request,
            )

        except Exception as error:
            workflow_error = _make_error(
                category=ErrorCategory.INTERNAL_ERROR,
                message=(
                    "An unexpected internal workflow "
                    "error occurred."
                ),
                retryable=None,
                details={
                    "internal_message": str(error),
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.INTERNAL,
                message=(
                    "The primary workflow failed "
                    "unexpectedly."
                ),
                session_id=session_id,
                user_input=user_input,
                report_name=report_name,
                variables=variables or [],
                error=workflow_error,
                diagnostics={
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )

    async def _execute(
        self,
        *,
        user_input: str,
        report_name: str,
        variables: list,
        email: str,
        session_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        user_role: Optional[str] = None,
        request_db_execution_tracker: Optional[
            RequestDatabaseExecutionTracker
        ] = None,
        recovery_hint: Optional[str] = None,
        prepared_request: Optional[PreparedRequest] = None,
    ) -> dict:
        """
        Internal primary workflow implementation.
        """

        # Local aliases keep the existing orchestration code
        # almost completely unchanged.
        pool = self.pool
        faq_searcher = self.faq_searcher
        workflow_store = self.workflow_store
        metadata_repository = (
            self.metadata_repository
        )
        oracle_schema_validator = (
            self.oracle_schema_validator
        )

        workflow_id: Optional[str] = None

    # ============================================================
    # STEP 1: Prepare and normalize the request
    # ============================================================

        try:
            if prepared_request is None:
                prepared_request = prepare_request(
                    user_input=user_input,
                    report_name=report_name,
                    variables=variables,
                )
        except QuestionNormalizationError as error:
            workflow_error = _make_error(
                category=ErrorCategory.INVALID_REQUEST,
                code=error.code,
                message=str(error),
                retryable=False,
                details=error.details,
            )
            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.REQUEST_VALIDATION,
                message=str(error),
                action="fix_variable_syntax",
                user_input=user_input,
                report_name=report_name,
                variables=list(variables or []),
                error=workflow_error,
            )

        normalized_variables = (
            prepared_request.normalized_variables
        )
        variable_names = prepared_request.variable_names
        variable_values=(prepared_request.variable_values)

        extra_filter_variables = (
            prepared_request
            .extra_filter_variables
        )

        extra_filter_names = (
            prepared_request
            .extra_filter_names
        )

        extra_filter_values = (
            prepared_request
            .extra_filter_values
        )
        query_without_values = (
            prepared_request.query_without_values
        )
        expected_variables_key = (
            prepared_request.variables_key
        )

        base_result_fields = {
            "session_id": session_id,
            "user_input": prepared_request.user_input,
            "report_name": prepared_request.report_name,
            "variables": normalized_variables,
            "variable_names": variable_names,
            "variable_values": variable_values,
            "normalized_input": query_without_values,
            "query_without_values": query_without_values,
        }

        # ============================================================
        # STEP 2: Validate the prepared request
        # ============================================================

        if not query_without_values:
            workflow_error = _make_error(
                category=ErrorCategory.INVALID_REQUEST,
                message="The normalized question is empty.",
                retryable=False,
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.REQUEST_VALIDATION,
                message=(
                    "The request did not contain a "
                    "searchable question."
                ),
                error=workflow_error,
                **base_result_fields,
            )

        # ============================================================
        # STEP 3: Get shared OpenAI resources
        # ============================================================

        try:
            client = await pool.get_client()
            semaphore = pool.semaphore

        except Exception as error:
            workflow_error = _make_error(
                category=(
                    ErrorCategory.RESOURCE_INITIALIZATION_ERROR
                ),
                message=(
                    "SQL-generation resources could not "
                    "be initialized."
                ),
                retryable=True,
                details={
                    "resource": "openai_pool",
                    "internal_message": str(error),
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )
            

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.RESOURCE_INITIALIZATION,
                message=(
                    "Failed to initialize "
                    "SQL-generation resources."
                ),
                error=workflow_error,
                **base_result_fields,
            )
        # ============================================================
        # STEP 4: Search the report-specific FAISS index
        # ============================================================

        search_result = await search_faqs(
            faq_searcher=faq_searcher,
            report_name=prepared_request.report_name,
            query_without_values=query_without_values,
            variable_names=variable_names,
            top_k=3,
        )

        print()
        print("=" * 80)
        print("[PRIMARY] FAISS SEARCH RESULT")
        print("=" * 80)
        print(
            json.dumps(
                search_result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        print("=" * 80)

        if search_result.get("status") != "success":
            message = search_result.get(
                "message",
                "FAQ search failed.",
            )

            workflow_error = _make_error(
                category=ErrorCategory.FAQ_SEARCH_ERROR,
                message=message,
                retryable=True,
                details={
                    "search_result": search_result,
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.FAQ_SEARCH,
                message=message,
                error=workflow_error,
                diagnostics={
                    "faq_search": search_result,
                },
                **base_result_fields,
            )

        # ============================================================
        # STEP 5: Validate and prepare FAQ examples
        # ============================================================

        (
            prepared_results,
            rejected_results,
        ) = prepare_faq_examples(
            search_result=search_result,
            report_name=prepared_request.report_name,
            expected_variables_key=(
                expected_variables_key
            ),
            variable_values=variable_values,
            top_k=3,
        )
        if not prepared_results:
            message = "No valid FAQ SQL examples were found."
            workflow_error = _make_error(
                category=ErrorCategory.NO_MATCHING_FAQ,
                message=message,
                retryable=False,
                details={
                    "expected_variables_key": expected_variables_key,
                    "rejected_results": rejected_results,
                },
            )
            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.FAQ_PREPARATION,
                message=message,
                error=workflow_error,
                diagnostics={
                    "faq_search": search_result,
                    "rejected_results": rejected_results,
                },
                **base_result_fields,
            )




        allowed_tables = (
            _extract_trusted_allowed_tables(
                prepared_results
            )
        )



        print()
        print("=" * 80)
        print("[PRIMARY] TRUSTED ALLOWED TABLES")
        print("=" * 80)
        print(
            json.dumps(
                allowed_tables,
                indent=2,
                ensure_ascii=False,
            )
        )
        print("=" * 80)


        if not allowed_tables:
            message = (
                "No trusted physical table could be "
                "resolved from the selected FAQ examples."
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_VALIDATION_ERROR
                ),
                code=(
                    "missing_trusted_table_allowlist"
                ),
                message=message,
                retryable=False,
                details={
                    "prepared_example_count": (
                        len(prepared_results)
                    ),
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_VALIDATION,
                message=message,
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "rejected_results": (
                        rejected_results
                    ),
                },
                **base_result_fields,
            )

        workflow_id = str(uuid.uuid4())

    # ============================================================
    # STEP 6: Create the workflow record
    # ============================================================

        try:
            await create_primary_workflow_record(
                workflow_store=workflow_store,
                workflow_id=workflow_id,
                session_id=session_id,
                chat_id=chat_id,
                email=email,
                user_role=user_role,
                report_name=prepared_request.report_name,
                user_input=prepared_request.user_input,
                query_without_values=query_without_values,
                variable_names=variable_names,
                variables_key=expected_variables_key,
                variable_values=variable_values,
                suggestions=prepared_results,
            )
        except Exception as error:
            workflow_error = _make_error(
                category=(
                    ErrorCategory.WORKFLOW_STORAGE_ERROR
                ),
                message=(
                    "The workflow record could not be "
                    "created."
                ),
                retryable=True,
                details={
                    "operation": "create_workflow",
                    "internal_message": str(error),
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )
            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.WORKFLOW_STORAGE,
                workflow_id=workflow_id,
                message="Failed to create the workflow record.",
                error=workflow_error,
                faiss_examples_used=len(prepared_results),
                diagnostics={
                    "rejected_results": rejected_results,
                },
                **base_result_fields,
            )

        # ============================================================
        # STEP 7: Generate or select the final SQL template
        # ============================================================

        try:
            final_sql_result = await generate_primary_sql(
                # SQL generation uses the same normalized business question
                # as FAISS. Structured variable names/values are supplied
                # separately and remain authoritative.
                new_question=query_without_values,
                examples=prepared_results,
                variable_names=variable_names,
                variable_values=variable_values,
                client=client,
                semaphore=semaphore,
                recovery_hint=recovery_hint,
            )
        except Exception as error:
            storage_warning = await safe_store_generation_error(
                workflow_store=workflow_store,
                workflow_id=workflow_id,
                error_message=str(error),
            )
            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_GENERATION_ERROR
                ),
                message=(
                    "SQL generation failed unexpectedly."
                ),
                retryable=True,
                details={
                    "internal_message": str(error),
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )
            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_GENERATION,
                workflow_id=workflow_id,
                message="SQL generation failed.",
                error=workflow_error,
                faiss_examples_used=len(prepared_results),
                diagnostics={"storage_warning": storage_warning},
                **base_result_fields,
            )

        print()
        print("=" * 80)
        print("[PRIMARY] FINAL SQL GENERATION RESULT")
        print("=" * 80)
        print(json.dumps(final_sql_result, indent=2, ensure_ascii=False, default=str))
        print("=" * 80)

        if final_sql_result.get("status") != "success":
            storage_warning = await safe_store_generation_result(
                workflow_store=workflow_store,
                workflow_id=workflow_id,
                generation_result=final_sql_result,
            )
            message = final_sql_result.get(
                "message",
                "Failed to generate final SQL.",
            )
            workflow_error = _make_error(
                category=ErrorCategory.SQL_GENERATION_ERROR,
                code=final_sql_result.get("error_type"),
                message=message,
                retryable=True,
                details={"sql_generation_result": final_sql_result},
            )
            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_GENERATION,
                workflow_id=workflow_id,
                message=message,
                sql_template=final_sql_result.get("sql_template") or None,
                action=final_sql_result.get("action"),
                selected_example=final_sql_result.get("selected_example"),
                error=workflow_error,
                faiss_examples_used=len(prepared_results),
                diagnostics={
                    "sql_generation_result": final_sql_result,
                    "storage_warning": storage_warning,
                },
                **base_result_fields,
            )

        final_sql_template = str(
            final_sql_result.get("sql_template", "")
        ).strip()

        if not final_sql_template:
            error_message = (
                "The final SQL template is empty."
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_GENERATION_ERROR
                ),
                code="empty_sql_template",
                message=error_message,
                retryable=True,
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_GENERATION,
                workflow_id=workflow_id,
                message=error_message,
                action=final_sql_result.get(
                    "action"
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,
                },
                **base_result_fields,
            )



        # ============================================================
        # STEP 7A: Apply report-configured option-variable SQL rules
        # ============================================================
        #
        # Roles and options.xlsx decides which variables are controlled
        # report options for the selected report. redis_mapping.xlsx /
        # redis mapping.xlsx decides which physical table/column each one
        # maps to.
        #
        # If a configured option value is supplied from ANY source, this
        # step makes its mapped-column predicate authoritative:
        #
        #     COLUMN LIKE '%{variable_name}%'
        #
        # If a configured option value is NOT supplied, this step removes
        # only an LLM-generated mapped-column predicate that still depends
        # on that unresolved placeholder. Hardcoded business predicates are
        # left untouched.

        report_option_filter_result = (
            apply_report_option_sql_filters(
                sql_template=final_sql_template,
                report_name=(
                    prepared_request.report_name
                ),
                variables=normalized_variables,
                allowed_tables=allowed_tables,
                dialect="oracle",
            )
        )

        print()
        print("=" * 80)
        print(
            "[PRIMARY] REPORT OPTION SQL FILTER RESULT"
        )
        print("=" * 80)
        print(
            json.dumps(
                report_option_filter_result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        print("=" * 80)

        if not report_option_filter_result.get(
            "success"
        ):
            error_message = (
                report_option_filter_result.get(
                    "message"
                )
                or (
                    "Failed to apply report option "
                    "rules to generated SQL."
                )
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_VALIDATION_ERROR
                ),
                code="report_option_sql_filter_failed",
                message=error_message,
                retryable=False,
                details={
                    "report_option_filter_result": (
                        report_option_filter_result
                    ),
                    "variables": normalized_variables,
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_VALIDATION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                query_executed=False,
                action=final_sql_result.get(
                    "action"
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,
                    "report_option_filter_result": (
                        report_option_filter_result
                    ),
                },
                **base_result_fields,
            )

        final_sql_template = str(
            report_option_filter_result.get(
                "sql_template",
                final_sql_template,
            )
        ).strip()


        # ============================================================
        # STEP 7B: Apply any remaining legacy source=option filters
        # ============================================================
        # Variables configured in Roles and options.xlsx have already been
        # handled authoritatively by STEP 7A, regardless of their source.
        # Keep the older generic option-filter service only as a fallback for
        # source=option variables that are not configured in that workbook.

        configured_report_option_names = {
            str(name or "").strip().lower()
            for name in (
                report_option_filter_result.get(
                    "configured_variable_names"
                )
                or []
            )
            if str(name or "").strip()
        }

        remaining_extra_filter_variables = [
            variable
            for variable in extra_filter_variables
            if str(
                variable.get("name") or ""
            ).strip().lower()
            not in configured_report_option_names
        ]

        extra_filter_result = (
            apply_extra_sql_filters(
                sql_template=final_sql_template,
                report_name=(
                    prepared_request.report_name
                ),
                extra_filters=(
                    remaining_extra_filter_variables
                ),
                allowed_tables=allowed_tables,
                dialect="oracle",
            )
        )

        print()
        print("=" * 80)
        print(
            "[PRIMARY] EXTRA SQL FILTER RESULT"
        )
        print("=" * 80)
        print(
            json.dumps(
                extra_filter_result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        print("=" * 80)

        if not extra_filter_result.get(
            "success"
        ):
            error_message = (
                extra_filter_result.get(
                    "message"
                )
                or (
                    "Failed to apply "
                    "option filters to SQL."
                )
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=(
                        workflow_store
                    ),
                    workflow_id=(
                        workflow_id
                    ),
                    error_message=(
                        error_message
                    ),
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory
                    .SQL_VALIDATION_ERROR
                ),
                code=(
                    "extra_sql_filter_failed"
                ),
                message=(
                    error_message
                ),
                retryable=False,
                details={
                    "extra_filter_result": (
                        extra_filter_result
                    ),
                    "extra_filter_variables": (
                        remaining_extra_filter_variables
                    ),
                    "report_option_filter_result": (
                        report_option_filter_result
                    ),
                },
            )

            return build_workflow_result(
                status=(
                    WorkflowStatus.FAILED
                ),
                stage=(
                    WorkflowStage
                    .SQL_VALIDATION
                ),
                workflow_id=(
                    workflow_id
                ),
                message=(
                    error_message
                ),
                sql_template=(
                    final_sql_template
                ),
                query_executed=False,
                action=(
                    final_sql_result.get(
                        "action"
                    )
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=(
                    workflow_error
                ),
                faiss_examples_used=(
                    len(
                        prepared_results
                    )
                ),
                diagnostics={
                    "storage_warning": (
                        storage_warning
                    ),
                    "report_option_filter_result": (
                        report_option_filter_result
                    ),
                    "extra_filter_result": (
                        extra_filter_result
                    ),
                },
                **base_result_fields,
            )

        final_sql_template = str(
            extra_filter_result.get(
                "sql_template",
                final_sql_template,
            )
        ).strip()


        execution_variable_values = {
            **variable_values,
            **extra_filter_values,
        }


        execution_variable_names = list(
            dict.fromkeys(
                [
                    *variable_names,
                    *extra_filter_names,
                ]
            )
        )









        # ============================================================
        # Resolve SQL placeholders to physical columns
        # ============================================================

        variable_binding_result = (
            resolve_variable_bindings(
                sql_template=final_sql_template,
                dialect="oracle",
            )
        )

        variable_binding_diagnostics = (
            variable_binding_result.model_dump(
                mode="json"
            )
        )


        if (
            not variable_binding_result.success
            or not variable_binding_result.complete
        ):
            error_message = (
                "Generated SQL variable placeholders "
                "could not be resolved to unique SQL "
                "predicate columns."
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_VALIDATION_ERROR
                ),
                code=(
                    "variable_binding_validation_failed"
                ),
                message=error_message,
                retryable=True,
                details={
                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_VALIDATION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                query_executed=False,
                action=final_sql_result.get(
                    "action"
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": (
                        storage_warning
                    ),
                    "allowed_tables": (
                        allowed_tables
                    ),
                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),
                },
                **base_result_fields,
            )

        # ============================================================
        # STEP 8: Inject request values into the SQL template
        # ============================================================

        injection_result = inject_sql_values(
            sql_template=final_sql_template,
            variable_values=(
                execution_variable_values
            )
        )

        if not injection_result.success:
            error_message = (
                injection_result.message
                or "Failed to inject variable values into SQL."
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            if injection_result.unresolved_placeholders:
                error_code = "unresolved_placeholders"
            elif not injection_result.sql_template:
                error_code = "empty_sql_template"
            else:
                error_code = "sql_injection_failed"

            workflow_error = _make_error(
                category=ErrorCategory.SQL_INJECTION_ERROR,
                code=error_code,
                message=error_message,
                retryable=True,
                details={
                    "unresolved_placeholders": (
                        injection_result.unresolved_placeholders
                    ),
                    "exception_type": (
                        injection_result.exception_type
                    ),
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_INJECTION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                sql=injection_result.executable_sql,
                action=final_sql_result.get("action"),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,
                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),                   
                    "injection_result": (
                        injection_result.model_dump()
                    ),
                },
                **base_result_fields,
            )

        executable_sql = (
            injection_result.executable_sql
        )

        if executable_sql is None:
            error_message = (
                "SQL injection reported success but did not "
                "return executable SQL."
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=ErrorCategory.SQL_INJECTION_ERROR,
                code="missing_executable_sql",
                message=error_message,
                retryable=True,
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_INJECTION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                action=final_sql_result.get("action"),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,

                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),

                    "injection_result": (
                        injection_result.model_dump(
                            mode="json"
                        )
                    ),
                },
                **base_result_fields,
            )







        # ============================================================
        # STEP 9: Validate generated SQL before Oracle execution
        # ============================================================

        sql_validation_result = validate_sql(
            sql=executable_sql,
            dialect="oracle",
            allowed_tables=allowed_tables,
        )

        if not sql_validation_result.valid:
            validation_issues = [
                issue.model_dump(
                    mode="json"
                )
                for issue in (
                    sql_validation_result.issues
                )
            ]

            issue_messages = [
                issue.message
                for issue in (
                    sql_validation_result.issues
                )
            ]

            error_message = (
                "Generated SQL failed deterministic "
                "validation."
            )

            if issue_messages:
                error_message = (
                    f"{error_message} "
                    + " | ".join(issue_messages)
                )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory.SQL_VALIDATION_ERROR
                ),
                code="sql_validation_failed",
                message=error_message,
                retryable=True,
                details={
                    "issues": validation_issues,
                    "statement_count": (
                        sql_validation_result
                        .statement_count
                    ),
                    "statement_types": (
                        sql_validation_result
                        .statement_types
                    ),
                    "tables": [
                        table.model_dump(
                            mode="json"
                        )
                        for table in (
                            sql_validation_result.tables
                        )
                    ],
                    "columns": [
                        column.model_dump(
                            mode="json"
                        )
                        for column in (
                            sql_validation_result.columns
                        )
                    ],
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.SQL_VALIDATION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                sql=executable_sql,
                query_executed=False,
                action=final_sql_result.get(
                    "action"
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,

                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),

                    "sql_validation": (
                        sql_validation_result.model_dump(
                            mode="json"
                        )
                    ),
                },
                **base_result_fields,
            )



        # ============================================================
        # STEP 9B: Validate physical schema against Oracle
        # ============================================================

        oracle_schema_validation_result = (
            await oracle_schema_validator(
                sql=executable_sql,
                sql_validation_result=(
                    sql_validation_result
                ),
                dialect="oracle",
                db_execution_tracker=(
                    request_db_execution_tracker
                ),
            )
        )

        oracle_schema_validation_diagnostics = (
            oracle_schema_validation_result
            .model_dump(
                mode="json"
            )
        )

        mapping_fallback_diagnostics = None

        # ============================================================
        # Oracle answered successfully.
        #
        # If Oracle says the table/column does not exist, Oracle wins.
        # Do NOT fall back to redis mapping.xlsx.
        # ============================================================

        if (
            oracle_schema_validation_result.success
            and not oracle_schema_validation_result.valid
        ):
            error_message = (
                oracle_schema_validation_result
                .message
            )

            first_issue = (
                oracle_schema_validation_result
                .issues[0]
                if (
                    oracle_schema_validation_result
                    .issues
                )
                else None
            )

            error_code = (
                first_issue.code
                if first_issue is not None
                else (
                    "oracle_schema_"
                    "validation_failed"
                )
            )

            storage_warning = (
                await safe_store_generation_error(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    error_message=error_message,
                )
            )

            workflow_error = _make_error(
                category=(
                    ErrorCategory
                    .SQL_VALIDATION_ERROR
                ),
                code=error_code,
                message=error_message,
                retryable=True,
                details={
                    "source_of_truth": (
                        "oracle"
                    ),
                    "oracle_schema_validation": (
                        oracle_schema_validation_diagnostics
                    ),
                },
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=(
                    WorkflowStage.SQL_VALIDATION
                ),
                workflow_id=workflow_id,
                message=error_message,
                sql_template=(
                    final_sql_template
                ),
                sql=executable_sql,
                query_executed=False,
                action=(
                    final_sql_result.get(
                        "action"
                    )
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": (
                        storage_warning
                    ),
                    "allowed_tables": (
                        allowed_tables
                    ),
                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),
                    "sql_validation": (
                        sql_validation_result
                        .model_dump(
                            mode="json"
                        )
                    ),
                    "oracle_schema_validation": (
                        oracle_schema_validation_diagnostics
                    ),
                    "schema_validation_source": (
                        "oracle"
                    ),
                },
                **base_result_fields,
            )

        # ============================================================
        # Oracle metadata lookup itself failed.
        #
        # NOW use redis mapping.xlsx as fallback knowledge.
        # ============================================================

        if (
            not oracle_schema_validation_result.success
        ):
            mapping_fallback_diagnostics = (
                validate_bindings_with_mapping_fallback(
                    binding_result=(
                        variable_binding_result
                    ),
                    report_name=(
                        prepared_request.report_name
                    ),
                )
            )

            # --------------------------------------------------------
            # Hard fallback conflict.
            #
            # Only happens when:
            #
            # same report
            # same variable
            # same table
            # workbook explicitly says different column
            # --------------------------------------------------------

            if (
                mapping_fallback_diagnostics.get(
                    "success"
                )
                and mapping_fallback_diagnostics.get(
                    "conflict"
                )
            ):
                error_message = (
                    "Oracle schema metadata was "
                    "unavailable and the fallback "
                    "variable mapping conflicts with "
                    "the generated SQL."
                )

                storage_warning = (
                    await safe_store_generation_error(
                        workflow_store=workflow_store,
                        workflow_id=workflow_id,
                        error_message=error_message,
                    )
                )

                workflow_error = _make_error(
                    category=(
                        ErrorCategory
                        .SQL_VALIDATION_ERROR
                    ),
                    code=(
                        "fallback_variable_"
                        "mapping_conflict"
                    ),
                    message=error_message,
                    retryable=True,
                    details={
                        "oracle_schema_validation": (
                            oracle_schema_validation_diagnostics
                        ),
                        "mapping_fallback": (
                            mapping_fallback_diagnostics
                        ),
                    },
                )

                return build_workflow_result(
                    status=(
                        WorkflowStatus.FAILED
                    ),
                    stage=(
                        WorkflowStage
                        .SQL_VALIDATION
                    ),
                    workflow_id=workflow_id,
                    message=error_message,
                    sql_template=(
                        final_sql_template
                    ),
                    sql=executable_sql,
                    query_executed=False,
                    error=workflow_error,
                    faiss_examples_used=len(
                        prepared_results
                    ),
                    diagnostics={
                        "storage_warning": (
                            storage_warning
                        ),
                        "variable_bindings": (
                            variable_binding_diagnostics
                        ),
                        "sql_validation": (
                            sql_validation_result
                            .model_dump(
                                mode="json"
                            )
                        ),
                        "oracle_schema_validation": (
                            oracle_schema_validation_diagnostics
                        ),
                        "mapping_fallback": (
                            mapping_fallback_diagnostics
                        ),
                        "schema_validation_source": (
                            "redis_mapping_fallback"
                        ),
                        "schema_validation_degraded": (
                            True
                        ),
                    },
                    **base_result_fields,
                )

        # ============================================================
        # Decide which validation source was used
        # ============================================================

        if (
            oracle_schema_validation_result.success
            and oracle_schema_validation_result.valid
        ):
            schema_validation_source = (
                "oracle"
            )

            schema_validation_degraded = (
                False
            )

        else:
            # Oracle metadata could not be checked.
            #
            # Mapping is only partial evidence because it validates
            # variable-bound columns, not every SELECT/filter column.
            #
            # Continue to actual Oracle execution. The execution itself
            # remains the final physical check.
            schema_validation_source = (
                "redis_mapping_fallback"
            )

            schema_validation_degraded = (
                True
            )
    # ============================================================
    # STEP 10: Execute the final SQL
    # ============================================================

        execution_retry_result = (
            await execute_primary_sql_with_retry(
                sql=executable_sql,
                row_limit=50,
                max_attempts=DEFAULT_ORACLE_EXECUTION_MAX_ATTEMPTS,
                base_delay_seconds=0.5,
                max_delay_seconds=2.0,
                db_execution_tracker=(
                    request_db_execution_tracker
                ),
            )
        )

        sql_execution_result = (
            execution_retry_result.final_result
        )

        execution_retry_diagnostics = (
            execution_retry_result.model_dump(
                mode="json"
            )
        )


        initial_execution_retry_diagnostics = (
            execution_retry_diagnostics
        )

        primary_sql_repair_diagnostics = None

        metadata_validation_diagnostics = None

        # Step 24: default/no-Oracle-error values so these are always
        # safely defined when building oracle_recovery_evidence below,
        # even when this failure never reaches the Oracle-classification
        # branch just below (e.g. a request-safety stop or a non-Oracle
        # execution error).
        should_attempt_sql_repair = False
        verified_repair_columns = []
        invalid_identifier_for_repair = None

        # Connection/session errors are already handled by
        # execute_primary_sql_with_retry(). This block only gathers
        # Oracle-classification evidence (Step 24) - it no longer
        # attempts an automatic internal repair.
        if (
            not sql_execution_result.success
            and sql_execution_result.error_kind
            == "oracle"
        ):
            initial_oracle_classification = (
                classify_oracle_error(
                    code=(
                        sql_execution_result
                        .error_code
                    ),
                    message=(
                        sql_execution_result
                        .error_message
                    ),
                    details=(
                        sql_execution_result
                        .error_details
                    ),
                )
            )

            # Step 24 Validation Fix: narrowed to the categories a
            # bounded semantic regeneration attempt can plausibly fix
            # (sql_syntax_error, invalid_grouping, bind_error -
            # ORACLE_SQL_SHAPE_RECOVERY_CATEGORIES above).
            # datatype_error/date_format_error also carry
            # recommended_action == "repair_sql" but this system
            # substitutes user-supplied values directly into these
            # expressions, and no evidence mechanism distinguishes a
            # bad value from a bad SQL shape for them, so they remain
            # deliberately excluded/terminal - see ORACLE RECOVERY
            # IMPLEMENTATION in the Step 24 Validation Fix report.
            should_attempt_sql_repair = (
                initial_oracle_classification
                .recommended_action
                == "repair_sql"
                and initial_oracle_classification.category
                in ORACLE_SQL_SHAPE_RECOVERY_CATEGORIES
            )

            verified_repair_columns = []
            invalid_identifier_for_repair = None

            if (
                initial_oracle_classification
                .recommended_action
                == "validate_metadata"
            ):
                metadata_validation_diagnostics = (
                    await _validate_primary_metadata_error(
                        metadata_repository=(
                            metadata_repository
                        ),
                        oracle_classification=(
                            initial_oracle_classification
                        ),
                        oracle_error_code=(
                            sql_execution_result
                            .error_code
                        ),
                        oracle_error_message=(
                            sql_execution_result
                            .error_message
                        ),
                        allowed_tables=(
                            allowed_tables
                        ),
                    )
                )

            if (
                initial_oracle_classification
                .category
                == "invalid_column"
                and metadata_validation_diagnostics
                is not None
                and metadata_validation_diagnostics
                .get("status")
                == "invalid_column_confirmed"
            ):
                invalid_identifier_for_repair = (
                    metadata_validation_diagnostics
                    .get("invalid_identifier")
                )

                verified_repair_columns = sorted(
                    {
                        str(
                            column.get(
                                "column_name",
                                "",
                            )
                        ).strip()
                        for column in (
                            metadata_validation_diagnostics
                            .get(
                                "available_columns",
                                [],
                            )
                        )
                        if str(
                            column.get(
                                "column_name",
                                "",
                            )
                        ).strip()
                    },
                    key=str.upper,
                )

                if (
                    invalid_identifier_for_repair
                    and verified_repair_columns
                ):
                    should_attempt_sql_repair = True

            # ========================================================
            # Step 24: the automatic internal Oracle SQL-repair-and-
            # re-execute branch that used to run here has been
            # removed (LEGACY SEMANTIC RECOVERY BYPASS).
            #
            # should_attempt_sql_repair (and, for the verified-
            # invalid-column case, verified_repair_columns /
            # invalid_identifier_for_repair) above are evidence
            # only now: they are attached to this failure's
            # diagnostics (oracle_recovery_evidence) for
            # ErrorStrategyResolver / ErrorStrategyActionExecutor to
            # use. The single owner of any Oracle semantic recovery
            # attempt is WorkflowManager's bounded
            # PrimarySemanticRecoveryBudget-gated loop, which
            # re-invokes this workflow with recovery_hint set - never
            # an internal repair-and-re-execute call from inside this
            # function.
            # ========================================================

        if not sql_execution_result.success:
            error_message = (
                sql_execution_result.error_message
                or "SQL execution failed."
            )

            stored_execution_result = {
                **final_sql_result,

                # final_sql_template may be the repaired template.
                "sql_template": (
                    final_sql_template
                ),

                "status": "execution_failed",

                # executable_sql may be the repaired executable SQL.
                "sql": executable_sql,
                "executable_sql": executable_sql,

                "query_executed": (
                    sql_execution_result.query_executed
                ),

                "execution_error": {
                    "kind": (
                        sql_execution_result.error_kind
                    ),
                    "code": (
                        sql_execution_result.error_code
                    ),
                    "message": error_message,
                    "details": (
                        sql_execution_result.error_details
                    ),
                    "exception_type": (
                        sql_execution_result.exception_type
                    ),
                },

                "results": [],
                "result_count": 0,

                "allowed_tables": (
                    allowed_tables
                ),

                "variable_bindings": (
                    variable_binding_diagnostics
                ),

                "sql_validation": (
                    sql_validation_result.model_dump(
                        mode="json"
                    )
                ),

                "oracle_schema_validation": (
                    oracle_schema_validation_diagnostics
                ),

                "mapping_fallback": (
                    mapping_fallback_diagnostics
                ),

                "schema_validation_source": (
                    schema_validation_source
                ),

                "schema_validation_degraded": (
                    schema_validation_degraded
                ),

                "initial_execution_retry": (
                    initial_execution_retry_diagnostics
                ),

                "execution_retry": (
                    execution_retry_diagnostics
                ),

                "primary_sql_repair": (
                    primary_sql_repair_diagnostics
                ),

                "metadata_validation": (
                    metadata_validation_diagnostics
                ),
            }

            storage_warning = (
                await safe_store_generation_result(
                    workflow_store=workflow_store,
                    workflow_id=workflow_id,
                    generation_result=(
                        stored_execution_result
                    ),
                )
            )

            oracle_classification = None

            if (
                sql_execution_result.error_kind
                == "request_safety"
            ):
                # Step 21: the request-level DB execution safety cap
                # was reached before this Oracle call could start.
                # This is a controlled, non-retryable safety stop -
                # not an Oracle error, and it must never trigger SQL
                # repair, another Oracle retry, or Secondary.
                error_category = (
                    ErrorCategory.EXECUTION_ERROR
                )

                error_retryable = False

            elif (
                sql_execution_result.error_kind
                == "oracle"
            ):
                error_category = (
                    ErrorCategory.ORACLE_DATABASE_ERROR
                )

                oracle_classification = (
                    classify_oracle_error(
                        code=(
                            sql_execution_result.error_code
                        ),
                        message=error_message,
                        details=(
                            sql_execution_result.error_details
                        ),
                    )
                )

                error_retryable = (
                    oracle_classification.retryable
                )

            else:
                error_category = (
                    ErrorCategory.EXECUTION_ERROR
                )

                error_retryable = None


            error_details = dict(
                sql_execution_result.error_details
                or {}
            )

            error_details.update(
                {
                    "error_kind": (
                        sql_execution_result.error_kind
                    ),
                    "exception_type": (
                        sql_execution_result.exception_type
                    ),
                    "query_executed": (
                        sql_execution_result.query_executed
                    ),
                }
            )

            if oracle_classification is not None:
                error_details[
                    "oracle_classification"
                ] = oracle_classification.model_dump(
                    mode="json"
                )

            # Step 24: safe, typed evidence for ErrorStrategyResolver's
            # Oracle-origin recovery classification. Never raw Oracle
            # text beyond what oracle_classification already exposes -
            # only a boolean eligibility flag plus, for the verified-
            # invalid-column case, the already-verified column names.
            oracle_recovery_evidence = (
                {
                    "eligible": bool(
                        should_attempt_sql_repair
                    ),
                    "verified_columns": (
                        verified_repair_columns
                    ),
                    "invalid_identifier": (
                        invalid_identifier_for_repair
                    ),
                }
                if oracle_classification is not None
                else None
            )

            workflow_error = _make_error(
                category=error_category,
                code=sql_execution_result.error_code,
                message=error_message,
                retryable=error_retryable,
                details=error_details,
            )

            return build_workflow_result(
                status=WorkflowStatus.FAILED,
                stage=WorkflowStage.EXECUTION,
                workflow_id=workflow_id,
                message=error_message,
                sql_template=final_sql_template,
                sql=executable_sql,
                query_executed=(
                    sql_execution_result.query_executed
                ),
                action=final_sql_result.get(
                    "action"
                ),
                selected_example=(
                    final_sql_result.get(
                        "selected_example"
                    )
                ),
                error=workflow_error,
                faiss_examples_used=len(
                    prepared_results
                ),
                diagnostics={
                    "storage_warning": storage_warning,

                    "allowed_tables": (
                        allowed_tables
                    ),

                    "initial_execution_retry": (
                        initial_execution_retry_diagnostics
                    ),

                    "execution_retry": (
                        execution_retry_diagnostics
                    ),

                    "primary_sql_repair": (
                        primary_sql_repair_diagnostics
                    ),


                    "metadata_validation": (
                        metadata_validation_diagnostics
                    ),
                    

                    "variable_bindings": (
                        variable_binding_diagnostics
                    ),

                    "sql_validation": (
                        sql_validation_result.model_dump(
                            mode="json"
                        )
                    ),

                    "oracle_schema_validation": (
                        oracle_schema_validation_diagnostics
                    ),

                    "mapping_fallback": (
                        mapping_fallback_diagnostics
                    ),

                    "schema_validation_source": (
                        schema_validation_source
                    ),

                    "schema_validation_degraded": (
                        schema_validation_degraded
                    ),

                    "execution_result": (
                        sql_execution_result.model_dump(
                            mode="json"
                        )
                    ),

                    "oracle_classification": (
                        oracle_classification.model_dump(
                            mode="json"
                        )
                        if oracle_classification
                        is not None
                        else None
                    ),

                    "oracle_recovery_evidence": (
                        oracle_recovery_evidence
                    ),
                },
                **base_result_fields,
            )





        query_results = (
            sql_execution_result.results
            or []
        )


        value_validation_result = None

        result_analysis_diagnostics = {
            "attempted": False,
            "success": False,
            "error": None,
        }


        if query_results:
            outcome_status = (
                WorkflowStatus.SUCCESS
            )

            result_analysis_diagnostics[
                "attempted"
            ] = True

            try:
                outcome_message = (
                    await generate_result_analysis(
                        user_input=(
                            prepared_request
                            .user_input
                        ),

                        report_name=(
                            prepared_request
                            .report_name
                        ),

                        rows=query_results,

                        client=client,

                        semaphore=semaphore,
                    )
                )

                result_analysis_diagnostics[
                    "success"
                ] = True

            except Exception as error:
                result_analysis_diagnostics[
                    "error"
                ] = {
                    "message": str(error),
                    "exception_type": (
                        type(error).__name__
                    ),
                }

                outcome_message = (
                    "The query executed successfully "
                    f"and returned "
                    f"{len(query_results)} row"
                    f"{'' if len(query_results) == 1 else 's'}. "
                    "Open the table to view the "
                    "detailed results."
                )

        else:
            outcome_status = (
                WorkflowStatus.NO_DATA
            )

            value_validation_result = (
                await validate_bound_values(
                    binding_result=(
                        variable_binding_result
                    ),
                    variable_values=(
                        execution_variable_values
                    ),
                    max_matches=10,
                    max_suggestions=5,
                    db_execution_tracker=(
                        request_db_execution_tracker
                    ),
                )
            )

            outcome_message = (
                value_validation_result.message
            )







        # Add it exactly here
        value_validation_diagnostics = (
            value_validation_result.model_dump(
                mode="json"
            )
            if value_validation_result
            is not None
            else None
        )



        # ============================================================
        # STEP 11: Store successful execution result
        # ============================================================

        final_execution_result = {
            **final_sql_result,

            # This may now contain the repaired SQL template.
            "sql_template": (
                final_sql_template
            ),

            "status": outcome_status.value,

            "sql": executable_sql,
            "executable_sql": executable_sql,

            "query_executed": (
                sql_execution_result.query_executed
            ),

            "result_count": (
                sql_execution_result.result_count
            ),

            "results": query_results,

            "allowed_tables": (
                allowed_tables
            ),

            "variable_bindings": (
                variable_binding_diagnostics
            ),

            "sql_validation": (
                sql_validation_result.model_dump(
                    mode="json"
                )
            ),

            "oracle_schema_validation": (
                oracle_schema_validation_diagnostics
            ),

            "mapping_fallback": (
                mapping_fallback_diagnostics
            ),

            "schema_validation_source": (
                schema_validation_source
            ),

            "schema_validation_degraded": (
                schema_validation_degraded
            ),

            "value_validation": (
                value_validation_diagnostics
            ),
            "report_option_filters": (
                report_option_filter_result
            ),
            "extra_sql_filters": (
                extra_filter_result
            ),
            "result_analysis": (
                result_analysis_diagnostics
            ),

            "initial_execution_retry": (
                initial_execution_retry_diagnostics
            ),

            "execution_retry": (
                execution_retry_diagnostics
            ),

            "primary_sql_repair": (
                primary_sql_repair_diagnostics
            ),

            "metadata_validation": (
                metadata_validation_diagnostics
            ),
        }

        storage_warning = (
            await safe_store_generation_result(
                workflow_store=workflow_store,
                workflow_id=workflow_id,
                generation_result=final_execution_result,
            )
        )
        print("=" * 80)
        print("Workflow ID:", workflow_id)
        print(
            "Result count:",
            sql_execution_result.result_count,
        )
        print(
            json.dumps(
                query_results,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        print("=" * 80)

        return build_workflow_result(
            status=outcome_status,
            stage=WorkflowStage.COMPLETED,
            workflow_id=workflow_id,
            message=outcome_message,
            action=final_sql_result.get(
                "action"
            ),
            selected_example=(
                final_sql_result.get(
                    "selected_example"
                )
            ),
            sql_template=final_sql_template,
            sql=executable_sql,
            query_executed=(
                sql_execution_result.query_executed
            ),
            results=query_results,
            faiss_examples_used=len(
                prepared_results
            ),
            diagnostics={
                "storage_warning": (
                    storage_warning
                ),

                "rejected_faq_results": (
                    rejected_results
                ),

                "allowed_tables": (
                    allowed_tables
                ),

                # Execution history of the original SQL.
                # This remains the original attempt even when repair succeeds.
                "initial_execution_retry": (
                    initial_execution_retry_diagnostics
                ),

                # Information about the single SQL repair attempt.
                # This is None when no repair was required.
                "primary_sql_repair": (
                    primary_sql_repair_diagnostics
                ),

                "metadata_validation": (
                    metadata_validation_diagnostics
                ),

                "variable_bindings": (
                    variable_binding_diagnostics
                ),

                "sql_validation": (
                    sql_validation_result.model_dump(
                        mode="json"
                    )
                ),
                "oracle_schema_validation": (
                    oracle_schema_validation_diagnostics
                ),

                "mapping_fallback": (
                    mapping_fallback_diagnostics
                ),

                "schema_validation_source": (
                    schema_validation_source
                ),

                "schema_validation_degraded": (
                    schema_validation_degraded
                ),

                "value_validation": (
                    value_validation_diagnostics
                ),

                "report_option_filters": (
                    report_option_filter_result
                ),

                "extra_sql_filters": (
                    extra_filter_result
                ),

                "result_analysis": (
                    result_analysis_diagnostics
                ),

                "execution": {
                    "row_limit": (
                        sql_execution_result.row_limit
                    ),
                    "result_count": (
                        sql_execution_result.result_count
                    ),
                    "attempt_count": (
                        execution_retry_result.attempt_count
                    ),
                    "retry_exhausted": (
                        execution_retry_result.exhausted
                    ),
                },

                # This is the final execution history.
                # After successful repair, it represents execution of
                # the repaired SQL.
                "execution_retry": (
                    execution_retry_diagnostics
                ),
            },
            **base_result_fields,
        )       