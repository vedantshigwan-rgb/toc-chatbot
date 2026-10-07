from __future__ import annotations

from enum import Enum


class WorkflowStatus(str, Enum):
    """High-level outcome shared by every workflow."""

    SUCCESS = "success"
    NO_DATA = "no_data"
    FAILED = "failed"


class WorkflowStage(str, Enum):
    """Stage at which the current workflow result was produced."""

    REQUEST_VALIDATION = "request_validation"
    RESOURCE_INITIALIZATION = "resource_initialization"
    FAQ_SEARCH = "faq_search"
    FAQ_PREPARATION = "faq_preparation"
    WORKFLOW_STORAGE = "workflow_storage"
    # NOTE (Step 26 validation fix): Secondary reuses this same value
    # to mean "the pre-SQL preparation boundary" (concept detection /
    # rule resolution / context construction), NOT that SQL has
    # actually been generated - workflows/secondary_workflow.py never
    # produces SQL text. No accurate, narrower stage value exists yet
    # for Secondary's pre-SQL stages, and adding one now would touch
    # this shared Primary/Secondary contract for a single caller.
    # Semantic stage cleanup (a dedicated Secondary pre-SQL stage
    # value, if warranted) is assigned to Step 30/31 alongside
    # QueryPlan/deterministic-SQL work.
    SQL_GENERATION = "sql_generation"
    SQL_INJECTION = "sql_injection"
    EXECUTION = "execution"
    COMPLETED = "completed"
    INTERNAL = "internal"
    SQL_VALIDATION = "sql_validation"
    # Step 33: LLM semantic SQL validation + bounded QueryPlan
    # correction. Distinct from SQL_VALIDATION (Step 32's static/
    # deterministic check) so the two stages remain honestly
    # distinguishable in WorkflowResult.stage.
    SEMANTIC_VALIDATION = "semantic_validation"
    # Step 34: Oracle physical schema validation of the FINAL Step-33
    # executable SQL. Distinct from SQL_VALIDATION (Step 32's static/
    # structural check, no live Oracle metadata) and
    # SEMANTIC_VALIDATION (Step 33's LLM check, no Oracle metadata
    # either).
    ORACLE_SCHEMA_VALIDATION = "oracle_schema_validation"


class ErrorCategory(str, Enum):
    """Initial error categories used by the common result contract.

    Detailed Oracle routing and retry classification will be added in the
    dedicated error-classification step. Step 1 only preserves the exact
    Oracle code and message instead of flattening them into a plain string.
    """

    INVALID_REQUEST = "invalid_request"
    RESOURCE_INITIALIZATION_ERROR = "resource_initialization_error"
    FAQ_SEARCH_ERROR = "faq_search_error"
    NO_MATCHING_FAQ = "no_matching_faq"
    WORKFLOW_STORAGE_ERROR = "workflow_storage_error"
    SQL_GENERATION_ERROR = "sql_generation_error"
    SQL_INJECTION_ERROR = "sql_injection_error"
    ORACLE_DATABASE_ERROR = "oracle_database_error"
    EXECUTION_ERROR = "execution_error"
    INTERNAL_ERROR = "internal_error"
    SQL_VALIDATION_ERROR = "sql_validation_error"
    SEMANTIC_VALIDATION_ERROR = "semantic_validation_error"
    ORACLE_SCHEMA_VALIDATION_ERROR = (
        "oracle_schema_validation_error"
    )


class OracleErrorCategory(str, Enum):
    INVALID_COLUMN = "invalid_column"
    INVALID_TABLE_OR_VIEW = "invalid_table_or_view"

    SQL_SYNTAX_ERROR = "sql_syntax_error"
    INVALID_GROUPING = "invalid_grouping"
    DATATYPE_ERROR = "datatype_error"
    DATE_FORMAT_ERROR = "date_format_error"
    BIND_ERROR = "bind_error"

    CONNECTION_UNAVAILABLE = "connection_unavailable"
    CONNECTION_CONFIGURATION_ERROR = (
        "connection_configuration_error"
    )
    SESSION_LOST = "session_lost"
    TIMEOUT = "timeout"

    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_ERROR = "permission_error"

    RESOURCE_BUSY = "resource_busy"
    DATABASE_INTERNAL_ERROR = "database_internal_error"

    QUERY_CANCELLED = "query_cancelled"
    UNKNOWN_DATABASE_ERROR = "unknown_database_error"


class RecoveryAction(str, Enum):
    VALIDATE_METADATA = "validate_metadata"
    REPAIR_SQL = "repair_sql"

    RETRY_EXECUTION = "retry_execution"
    RETRY_CONNECTION = "retry_connection"

    CLARIFY_INPUT = "clarify_input"
    STOP = "stop"
    INVESTIGATE = "investigate"


class SQLValidationIssueCategory(str, Enum):
    EMPTY_SQL = "empty_sql"
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NON_QUERY_STATEMENT = "non_query_statement"
    UNSAFE_OPERATION = "unsafe_operation"
    DISALLOWED_TABLE = "disallowed_table"
    UNRESOLVED_PLACEHOLDER = "unresolved_placeholder"





class ValueCheckStatus(str, Enum):
    FOUND = "found"
    MISSING = "missing"
    SKIPPED = "skipped"
    ERROR = "error"


class ValueValidationStatus(str, Enum):
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"
    SKIPPED = "skipped"
    FAILED = "failed"


class AdequacyVerdict(str, Enum):
    """Result-quality verdict produced by the Result Adequacy checker."""

    FULLY_ADEQUATE = "fully_adequate"
    PARTIAL = "partial"
    INADEQUATE = "inadequate"
    UNRESOLVED = "unresolved"


class NoDataDiagnosis(str, Enum):
    ALL_VALUES_EXIST = "all_values_exist"
    SOME_VALUES_MISSING = "some_values_missing"
    ALL_VALUES_MISSING = "all_values_missing"

    NO_VARIABLES = "no_variables"
    BINDINGS_INCOMPLETE = "bindings_incomplete"
    VALIDATION_FAILED = "validation_failed"



class GraphIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"

class ConceptDetectionSource(str, Enum):
    DETERMINISTIC = "deterministic"
    VECTOR = "vector"
    LLM = "llm"



class SecondaryRuleStatus(str, Enum):
    SELECTED = "selected"
    BLOCKED = "blocked"
    ELIGIBLE = "eligible"
    SKIPPED = "skipped"
    CONFLICTING = "conflicting"


class SecondaryContextIssueSeverity(
    str,
    Enum,
):
    ERROR = "error"
    WARNING = "warning"


class FeedbackOperationType(str, Enum):
    """
    How a feedback message changes one previous variable or one
    generic request requirement. Shared vocabulary for both
    FeedbackVariableOperation and FeedbackRequestOperation.
    """

    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    RETAIN = "retain"


class FeedbackRequestCategory(str, Enum):
    """
    Behavioral (not schema-specific) category of a generic feedback
    request-operation - what kind of requirement changed.
    """

    FILTER = "filter"
    REQUESTED_OUTPUT = "requested_output"
    GROUPING = "grouping"
    ORDERING = "ordering"
    LIMIT = "limit"
    CONDITION = "condition"
    OTHER = "other"


class ClarificationStatus(str, Enum):
    """
    Lifecycle state of a clarification request/checkpoint. Deliberately
    independent of WorkflowStatus - a clarification's own lifecycle
    (pending/resolved/cancelled/expired) is a separate concern from
    the outcome of the workflow request that produced it.
    """

    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ContinuationDispatchStatus(str, Enum):
    """
    Execution lifecycle of the continuation HANDLER for a resolved
    clarification response - deliberately separate from
    ClarificationStatus (which tracks the clarification's own
    accept/cancel/expire lifecycle). A clarification's values can be
    valid and accepted without its continuation handler having
    successfully run yet; this enum tracks that second, independent
    dimension so retry/idempotency decisions never have to overload
    ClarificationStatus.
    """

    NOT_STARTED = "not_started"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class ClarificationInputType(str, Enum):
    """
    Generic, business-agnostic input shapes a ClarificationField can
    request from the user. Do not add business-specific field types
    here - only structural input kinds.
    """

    TEXT = "text"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    NUMBER = "number"
    DATE = "date"
    DATE_RANGE = "date_range"
    BOOLEAN = "boolean"


class FeedbackExecutionRoute(str, Enum):
    """
    Where a resolved feedback request should be executed. Decided by
    FeedbackExecutionDecisionService. The route this task chooses is
    not itself executed in this step - later roadmap steps do that.
    """

    EXISTING_RESULT_ONLY = "existing_result_only"
    DATABASE_REEXECUTION_REQUIRED = (
        "database_reexecution_required"
    )
    CLARIFICATION_REQUIRED = "clarification_required"


class MetadataGovernanceSeverity(str, Enum):
    """
    Generic three-level severity for Step 38 metadata governance
    findings. Deliberately separate from GraphIssueSeverity /
    SecondaryContextIssueSeverity (both narrower, Secondary-specific,
    two-level ERROR/WARNING enums) - governance findings span every
    metadata source, not just Secondary rule graphs, and need an
    INFO tier available for future use alongside WARNING (drift that
    remains repairable through the existing controlled sync path)
    and ERROR (a genuine configuration/physical-invalidity problem).
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MetadataGovernanceIssueCode(str, Enum):
    """
    Deterministic Step 38 metadata governance issue classification.
    Every value is produced by a concrete, reproducible comparison
    against an authoritative or physical source - never assigned by
    an LLM.
    """

    SOURCE_UNAVAILABLE = "source_unavailable"
    SCHEMA_INVALID = "schema_invalid"
    DUPLICATE_IDENTIFIER = "duplicate_identifier"
    DUPLICATE_REPORT = "duplicate_report"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    STALE_DERIVED_METADATA = "stale_derived_metadata"
    MISSING_DERIVED_METADATA = "missing_derived_metadata"
    ORPHAN_DERIVED_METADATA = "orphan_derived_metadata"
    SOURCE_HASH_MISMATCH = "source_hash_mismatch"
    REPORT_SCOPE_MISMATCH = "report_scope_mismatch"
    IDENTIFIER_MISMATCH = "identifier_mismatch"
    PHYSICAL_TABLE_NOT_FOUND = "physical_table_not_found"
    PHYSICAL_COLUMN_NOT_FOUND = "physical_column_not_found"
    MAPPING_TARGET_INVALID = "mapping_target_invalid"
    CONFIGURATION_CONFLICT = "configuration_conflict"