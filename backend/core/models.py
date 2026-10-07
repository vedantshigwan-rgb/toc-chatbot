from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from core.enums import (ErrorCategory, WorkflowStage, WorkflowStatus, OracleErrorCategory,
    RecoveryAction, SQLValidationIssueCategory, NoDataDiagnosis, ValueCheckStatus, ValueValidationStatus,GraphIssueSeverity,ConceptDetectionSource,SecondaryRuleStatus,SecondaryContextIssueSeverity,
    FeedbackOperationType, FeedbackRequestCategory, FeedbackExecutionRoute,
    MetadataGovernanceSeverity, MetadataGovernanceIssueCode,)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        use_enum_values=True,
        extra="allow",
    )


class WorkflowError(ContractModel):
    category: ErrorCategory
    code: Optional[str] = None
    message: str
    retryable: Optional[bool] = None
    details: Dict[str, Any] = Field(default_factory=dict)



class PreparedRequest(ContractModel):
    user_input: str
    report_name: str

    # All variables received from frontend.
    normalized_variables: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list
    )

    # Variables belonging to the actual user question.
    # These are used for:
    # - FAISS index selection
    # - FAQ preparation
    # - SQL generation
    variable_names: List[str] = Field(
        default_factory=list
    )

    variable_values: Dict[
        str,
        Any,
    ] = Field(
        default_factory=dict
    )

    # Sidebar option variables.
    # These must NOT participate in FAISS or base SQL generation.
    extra_filter_variables: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list
    )

    extra_filter_names: List[str] = Field(
        default_factory=list
    )

    extra_filter_values: Dict[
        str,
        Any,
    ] = Field(
        default_factory=dict
    )

    # Canonical normalized question used for FAISS retrieval.
    normalized_input: str

    # Backward-compatible alias used throughout the existing Primary flow.
    query_without_values: str

    # Built using ONLY input/question variables.
    variables_key: str

class SQLInjectionResult(ContractModel):
    success: bool

    sql_template: str
    executable_sql: Optional[str] = None

    unresolved_placeholders: List[str] = Field(
        default_factory=list
    )

    message: Optional[str] = None
    exception_type: Optional[str] = None





class SQLExecutionResult(ContractModel):
    success: bool
    query_executed: bool = False

    sql: str
    row_limit: int = 50

    result_count: int = 0
    results: List[Dict[str, Any]] = Field(
        default_factory=list
    )

    error_kind: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_details: Dict[str, Any] = Field(
        default_factory=dict
    )
    exception_type: Optional[str] = None



class OracleErrorClassification(ContractModel):
    code: Optional[str] = None

    category: OracleErrorCategory
    retryable: bool
    recommended_action: RecoveryAction

    summary: str
    matched_by: str

    original_message: Optional[str] = None

    details: Dict[str, Any] = Field(
        default_factory=dict
    )






class ColumnMetadataRecord(ContractModel):
    """
    One normalized ColumnMetadata object retrieved from Weaviate.
    """

    uuid: Optional[str] = None
    custom_id: Optional[str] = None

    module: str
    table_name: str
    column_name: str

    data_type: Optional[str] = None
    description: Optional[str] = None
    text: Optional[str] = None

    distinct_values: List[Any] = Field(
        default_factory=list
    )
    units: List[Any] = Field(
        default_factory=list
    )
    role_access: List[Any] = Field(
        default_factory=list
    )

    used: int = 0
    filterable: bool = False

    vector_distance: Optional[float] = None


class MetadataRepositoryResult(ContractModel):
    """
    Common result returned by Weaviate metadata operations.
    """

    success: bool
    found: bool = False

    operation: str

    records: List[ColumnMetadataRecord] = Field(
        default_factory=list
    )

    message: Optional[str] = None
    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )











class WorkflowAttempt(ContractModel):
    attempt_number: int = 1
    workflow: str = "primary"
    status: WorkflowStatus
    stage: WorkflowStage
    message: str
    sql_template: Optional[str] = None
    sql: Optional[str] = None
    query_executed: bool = False
    result_count: int = 0
    error: Optional[WorkflowError] = None


class WorkflowResult(ContractModel):
    """Stable result returned by primary, repair and secondary workflows."""

    workflow: str = "primary"
    status: WorkflowStatus
    stage: WorkflowStage

    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    user_input: Optional[str] = None
    report_name: Optional[str] = None
    message: str

    action: Optional[str] = None
    selected_example: Optional[int] = None

    sql_template: Optional[str] = None
    sql: Optional[str] = None
    query_executed: bool = False

    result_count: int = 0
    results: List[Dict[str, Any]] = Field(default_factory=list)

    variables: List[Dict[str, Any]] = Field(default_factory=list)
    variable_names: List[str] = Field(default_factory=list)
    variable_values: Dict[str, Any] = Field(default_factory=dict)
    normalized_input: Optional[str] = None
    query_without_values: Optional[str] = None
    faiss_examples_used: int = 0

    error: Optional[WorkflowError] = None
    attempts: List[WorkflowAttempt] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        # mode="python" preserves arbitrary database values. FastAPI's
        # jsonable_encoder handles dates and decimals at the API boundary.
        return self.model_dump(mode="python")


def build_workflow_result(
    *,
    status: WorkflowStatus,
    stage: WorkflowStage,
    message: str,
    workflow: str = "primary",
    workflow_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_input: Optional[str] = None,
    report_name: Optional[str] = None,
    action: Optional[str] = None,
    selected_example: Optional[int] = None,
    sql_template: Optional[str] = None,
    sql: Optional[str] = None,
    query_executed: bool = False,
    results: Optional[List[Dict[str, Any]]] = None,
    variables: Optional[List[Dict[str, Any]]] = None,
    variable_names: Optional[List[str]] = None,
    variable_values: Optional[Dict[str, Any]] = None,
    normalized_input: Optional[str] = None,
    query_without_values: Optional[str] = None,
    faiss_examples_used: int = 0,
    error: Optional[WorkflowError] = None,
    attempts: Optional[List[WorkflowAttempt]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result_rows = list(results or [])

    if attempts is None:
        attempts = [
            WorkflowAttempt(
                attempt_number=1,
                workflow=workflow,
                status=status,
                stage=stage,
                message=message,
                sql_template=sql_template,
                sql=sql,
                query_executed=query_executed,
                result_count=len(result_rows),
                error=error,
            )
        ]

    result = WorkflowResult(
        workflow=workflow,
        status=status,
        stage=stage,
        workflow_id=workflow_id,
        session_id=session_id,
        user_input=user_input,
        report_name=report_name,
        message=message,
        action=action,
        selected_example=selected_example,
        sql_template=sql_template,
        sql=sql,
        query_executed=query_executed,
        result_count=len(result_rows),
        results=result_rows,
        variables=list(variables or []),
        variable_names=list(variable_names or []),
        variable_values=dict(variable_values or {}),
        normalized_input=(
            normalized_input
            if normalized_input is not None
            else query_without_values
        ),
        query_without_values=query_without_values,
        faiss_examples_used=faiss_examples_used,
        error=error,
        attempts=attempts,
        diagnostics=dict(diagnostics or {}),
    )

    return result.to_dict()







class SQLValidationIssue(ContractModel):
    category: SQLValidationIssueCategory
    code: Optional[str] = None
    message: str

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class SQLTableReference(ContractModel):
    name: str
    schema_name: Optional[str] = None
    catalog_name: Optional[str] = None

    qualified_name: str
    alias: Optional[str] = None


class SQLColumnReference(ContractModel):
    name: str
    table_alias: Optional[str] = None
    qualified_name: str
    is_star: bool = False


class SQLValidationResult(ContractModel):
    valid: bool
    dialect: str = "oracle"

    statement_count: int = 0
    statement_types: List[str] = Field(
        default_factory=list
    )

    tables: List[SQLTableReference] = Field(
        default_factory=list
    )

    columns: List[SQLColumnReference] = Field(
        default_factory=list
    )

    functions: List[str] = Field(
        default_factory=list
    )

    unresolved_placeholders: List[str] = Field(
        default_factory=list
    )

    issues: List[SQLValidationIssue] = Field(
        default_factory=list
    )

    normalized_sql: Optional[str] = None



class OracleSchemaValidationIssue(
    ContractModel
):
    code: str
    message: str

    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    column_name: Optional[str] = None

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class OracleSchemaValidationResult(
    ContractModel
):
    success: bool
    valid: bool

    source: str = "oracle"
    authoritative: bool = True

    checked_tables: List[str] = Field(
        default_factory=list
    )

    available_columns: Dict[
        str,
        List[str],
    ] = Field(
        default_factory=dict
    )

    issues: List[
        OracleSchemaValidationIssue
    ] = Field(
        default_factory=list
    )

    message: str

    error_code: Optional[str] = None
    error_message: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


    

class SQLVariableBinding(ContractModel):
    variable_name: str

    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    qualified_table_name: Optional[str] = None
    table_alias: Optional[str] = None

    column_name: str
    operator: str
    match_type: str

    placeholder_count: int = 1
    predicate_sql: Optional[str] = None


class VariableBindingIssue(ContractModel):
    code: str
    message: str

    variable_name: Optional[str] = None

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class VariableBindingResolutionResult(
    ContractModel
):
    success: bool
    complete: bool

    dialect: str = "oracle"

    placeholder_names: List[str] = Field(
        default_factory=list
    )

    bindings: List[
        SQLVariableBinding
    ] = Field(
        default_factory=list
    )

    unresolved_variables: List[str] = Field(
        default_factory=list
    )

    ambiguous_variables: List[str] = Field(
        default_factory=list
    )

    issues: List[
        VariableBindingIssue
    ] = Field(
        default_factory=list
    )

    masked_sql: Optional[str] = None





class ValueValidationItem(ContractModel):
    variable_name: str
    submitted_value: Any

    status: ValueCheckStatus
    found: bool = False

    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    column_name: Optional[str] = None

    operator: Optional[str] = None
    match_type: Optional[str] = None

    matched_values: List[Any] = Field(
        default_factory=list
    )

    suggested_values: List[Any] = Field(
        default_factory=list
    )

    validation_sql: Optional[str] = None

    error_code: Optional[str] = None
    error_message: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class ValueValidationResult(ContractModel):
    success: bool

    status: ValueValidationStatus
    diagnosis: NoDataDiagnosis

    message: str

    total_value_count: int = 0
    checked_count: int = 0

    found_count: int = 0
    missing_count: int = 0
    error_count: int = 0
    skipped_count: int = 0

    items: List[
        ValueValidationItem
    ] = Field(
        default_factory=list
    )

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )




class SecondaryGraphConcept(ContractModel):
    name: str
    description: str = ""

    activates_rules: List[str] = Field(
        default_factory=list
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )

    raw: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryGraphRule(ContractModel):
    name: str
    description: str = ""

    applies_when_concepts: List[str] = Field(
        default_factory=list
    )

    # Normalized convenience fields. These are extracted from
    # the flexible "enforces" section.
    tables: List[str] = Field(
        default_factory=list
    )

    columns: List[str] = Field(
        default_factory=list
    )

    enforces: Dict[str, Any] = Field(
        default_factory=dict
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )

    constraints: List[Any] = Field(
        default_factory=list
    )

    raw: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryGraph(ContractModel):
    source_path: str

    concepts: Dict[
        str,
        SecondaryGraphConcept,
    ] = Field(
        default_factory=dict
    )

    tables: Dict[
        str,
        Dict[str, Any],
    ] = Field(
        default_factory=dict
    )

    columns: Dict[
        str,
        List[str],
    ] = Field(
        default_factory=dict
    )

    rules: Dict[
        str,
        SecondaryGraphRule,
    ] = Field(
        default_factory=dict
    )

    raw: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryGraphValidationIssue(
    ContractModel
):
    severity: GraphIssueSeverity

    code: str
    message: str

    concept_name: Optional[str] = None
    rule_name: Optional[str] = None
    table_name: Optional[str] = None
    column_name: Optional[str] = None

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryGraphValidationResult(
    ContractModel
):
    valid: bool

    error_count: int = 0
    warning_count: int = 0

    issues: List[
        SecondaryGraphValidationIssue
    ] = Field(
        default_factory=list
    )

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryGraphLoadResult(ContractModel):
    success: bool

    graph: Optional[
        SecondaryGraph
    ] = None

    validation: Optional[
        SecondaryGraphValidationResult
    ] = None

    message: str

    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )








class SecondaryDetectedConcept(ContractModel):
    name: str
    description: str = ""

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    sources: List[
        ConceptDetectionSource
    ] = Field(
        default_factory=list
    )

    evidence: List[str] = Field(
        default_factory=list
    )

    activated_rules: List[str] = Field(
        default_factory=list
    )


class SecondaryConceptDetectionResult(
    ContractModel
):
    success: bool

    question: str
    normalized_question: str

    concepts: List[
        SecondaryDetectedConcept
    ] = Field(
        default_factory=list
    )

    concept_names: List[str] = Field(
        default_factory=list
    )

    requires_clarification: bool = False

    rejected_llm_concepts: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list
    )

    warnings: List[str] = Field(
        default_factory=list
    )

    message: str

    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 27 - independent Weaviate / LLM concept selectors.
#
# These are deliberately SEPARATE from SecondaryConceptDetectionResult
# above (the retired, coupled Step-26 detector's output shape). Step 27
# never merges/unions the two selectors' outputs - that is Step 28's
# responsibility. Each selection result differentiates a legitimate
# empty selection ([], success=True) from a technical failure
# (success=False, error_code set).
# ================================================================


class SecondaryWeaviateConceptSelection(
    ContractModel
):
    """
    Raw output of the independent, report-scoped Weaviate selector.

    STEP-29 TRUST BOUNDARY (Step 28 validation fix, Fix 5):
    `selected_concepts` here is NOT trusted input for any downstream
    (Step 29+) processing - it may contain unknown or cross-report
    labels. Only `SecondaryConceptValidationResult.final_concepts`,
    produced by the Step-28 validator, is trusted.
    """

    success: bool

    selected_concepts: List[str] = Field(
        default_factory=list
    )

    candidate_count: int = 0

    message: Optional[str] = None
    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryLLMConceptSelection(
    ContractModel
):
    """
    Raw output of the independent LLM selector, which always sees the
    FULL selected-report concept catalogue and NEVER sees the Weaviate
    selector's output.

    STEP-29 TRUST BOUNDARY (Step 28 validation fix, Fix 5):
    `selected_concepts` here is NOT trusted input for any downstream
    (Step 29+) processing - a syntactically valid label may still be
    unknown or belong to a different report. Only
    `SecondaryConceptValidationResult.final_concepts`, produced by the
    Step-28 validator, is trusted.
    """

    success: bool

    selected_concepts: List[str] = Field(
        default_factory=list
    )

    catalogue_size: int = 0

    confidence: Optional[float] = None

    message: Optional[str] = None
    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryConceptSelectionResult(
    ContractModel
):
    """
    Step-27 top-level result: exact report identity plus the two
    independent, UNMERGED selector outputs. There is no final_concepts
    field here by design - Step 28 owns validation + union.
    """

    success: bool

    report_name: str
    report_key: str

    catalogue_size: int = 0

    weaviate_selection: Optional[
        SecondaryWeaviateConceptSelection
    ] = None

    llm_selection: Optional[
        SecondaryLLMConceptSelection
    ] = None

    message: Optional[str] = None
    error_code: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 28 - deterministic concept OUTPUT validation + exact union.
#
# Field naming is deliberately layered so a caller can never confuse
# what an LLM/Weaviate selector RAW-returned, what one source's
# concepts validated to, and what is FINALLY trusted:
#
#   raw_concepts        -> exactly what Step 27 returned, untouched
#   validated_concepts   -> one source's concepts, ONLY when ALL of
#                            that source's concepts are valid
#   final_concepts       -> the exact validated union, ONLY on overall
#                            success; always [] on failure (never a
#                            partially-valid subset)
# ================================================================


class SecondaryConceptValidationIssue(
    ContractModel
):
    """One concept-level validation problem found during Step 28."""

    concept: str

    issue_code: str
    source: str

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondarySourceConceptValidation(
    ContractModel
):
    """
    Step-28 validation outcome for ONE selector's raw concepts against
    the exact selected report's authoritative catalogue.
    """

    success: bool

    raw_concepts: List[str] = Field(
        default_factory=list
    )

    validated_concepts: List[str] = Field(
        default_factory=list
    )

    issues: List[
        SecondaryConceptValidationIssue
    ] = Field(default_factory=list)


class SecondaryConceptValidationResult(
    ContractModel
):
    """
    Step-28 top-level result: exact-report-identity-checked validation
    of both Step-27 selector outputs plus their exact deterministic
    union. `final_concepts` contains CANONICAL concept names only and
    is populated ONLY on overall success.

    STEP-29 TRUST BOUNDARY (Step 28 validation fix, Fix 5): this is
    the ONLY object/field a future Step 29 may treat as trusted
    concept input - specifically `final_concepts` on a result where
    `success is True`. Step 29 must NOT consume
    `weaviate_selection.selected_concepts`,
    `llm_selection.selected_concepts`, any source's `raw_concepts`, or
    anything under `diagnostics.concept_selection` as if it were
    validated - those remain raw, pre-validation values.
    """

    success: bool

    report_name: str
    report_key: str

    weaviate_validation: Optional[
        SecondarySourceConceptValidation
    ] = None

    llm_validation: Optional[
        SecondarySourceConceptValidation
    ] = None

    final_concepts: List[str] = Field(
        default_factory=list
    )

    message: Optional[str] = None
    error_code: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 29 - deterministic concept -> activated-rule -> winning query
# case resolution, ported from the authoritative reference's
# deterministic_report_sql.py::resolve_case(). Deliberately named and
# shaped differently from the legacy SecondaryResolvedRule/
# SecondaryRuleResolutionResult below (the old applies_when
# fragment-merge model) - this is a NEW, unrelated result: exactly ONE
# winning query case, never a merged set of rule fragments.
# ================================================================


class SecondaryActivatedRule(ContractModel):
    """
    One rule activated by the trusted Step-28 final_concepts, with
    provenance. Provenance is diagnostic only - it must never
    influence case scoring (the reference does not weight by how many
    concepts activated a rule).
    """

    rule_name: str

    activated_by: List[str] = Field(
        default_factory=list
    )


class SecondaryQueryCaseResolutionResult(
    ContractModel
):
    """
    Step-29 typed output. On success, `winning_query_spec` is a frozen
    (deep-copied) representation of the authoritative YAML case's own
    `query` mapping - unflattened, not converted to SQL or
    SecondaryQueryPlan. Step 30 consumes this object directly, never
    WorkflowResult diagnostics.
    """

    success: bool

    report_name: str
    report_key: str

    final_concepts: List[str] = Field(
        default_factory=list
    )

    activated_rules: List[
        SecondaryActivatedRule
    ] = Field(default_factory=list)

    winning_rule: Optional[str] = None
    winning_case_id: Optional[str] = None
    winning_query_spec: Optional[
        Dict[str, Any]
    ] = None

    match_score: Optional[float] = None
    matched_conditions: Dict[str, Any] = Field(
        default_factory=dict
    )

    message: Optional[str] = None
    error_code: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 30 - typed QueryPlan normalization of the Step-29 winning
# authoritative query specification. One shape-specific typed model
# per query_shape actually used by the authoritative snapshot
# (single_select/union/nested_select/ranked_latest/raw_template).
# These carry ONLY fields that exist in the authoritative YAML query
# specification - no invented physical identifiers, no rendered SQL.
# ================================================================


class SecondarySingleSelectPlan(ContractModel):
    """Normalized single_select query specification. No SQL rendered."""

    table: str
    columns: List[str] = Field(default_factory=list)
    distinct: bool = False
    filters: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    having: List[str] = Field(default_factory=list)
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None


class SecondaryUnionPlan(ContractModel):
    """
    Normalized union query specification. Each branch is preserved
    separately, in reference-semantic order - never merged/flattened.
    """

    union_type: str = "UNION"
    branches: List[SecondarySingleSelectPlan] = Field(
        default_factory=list
    )
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None


class SecondaryNestedSelectPlan(ContractModel):
    """
    Normalized nested_select query specification. `inner` is preserved
    as its own complete single_select structure - never flattened into
    the outer query.
    """

    inner: SecondarySingleSelectPlan
    columns: List[str] = Field(default_factory=list)
    distinct: bool = False
    filters: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    having: List[str] = Field(default_factory=list)
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    # Some authoritative specs carry a redundant outer `table` value
    # alongside `inner.table` - preserved verbatim when present, even
    # though the reference's own nested_select renderer never reads it
    # (FROM uses the rendered inner subquery, not this field).
    table: Optional[str] = None


class SecondaryRankedLatestPlan(ContractModel):
    """
    Normalized ranked_latest query specification - the exact
    partition/rank/window semantics the reference requires, not
    approximated as ordinary ordering.
    """

    table: str
    cte_name: str = "RANKED_DATA"
    base_columns: List[str] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)
    partition_by: List[str] = Field(default_factory=list)
    rank_order_by: List[str] = Field(default_factory=list)
    rank_alias: str = "RN"
    outer_columns: List[str] = Field(default_factory=list)
    rank_value: int = 1
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None


class SecondaryRawTemplatePlan(ContractModel):
    """
    Normalized raw_template query specification. `sql` is the RAW,
    UNRENDERED authoritative template text carried forward verbatim -
    Step 30 never substitutes/executes it. Only the authoritative
    winning YAML case may ever supply this value.
    """

    sql: str


class SecondaryQueryPlan(ContractModel):
    """
    Step-30 typed output: the Step-29 winning authoritative query
    specification normalized into a strongly typed plan. Contains NO
    generated SQL, no rendered clauses, no bound values - `plan_spec`
    is a structural representation only.

    STEP-31 TRUST BOUNDARY: this is the ONLY object Step 31 may
    consume to build SQL - never WorkflowResult diagnostics, never a
    re-parsed `winning_query_spec` dict, never raw YAML re-reading.
    """

    success: bool

    report_name: str
    report_key: str

    final_concepts: List[str] = Field(
        default_factory=list
    )

    winning_rule: Optional[str] = None
    winning_case_id: Optional[str] = None

    query_shape: Optional[str] = None
    plan_spec: Optional[
        Union[
            SecondarySingleSelectPlan,
            SecondaryUnionPlan,
            SecondaryNestedSelectPlan,
            SecondaryRankedLatestPlan,
            SecondaryRawTemplatePlan,
        ]
    ] = None

    # Trusted supplied variable NAMES only (never values/bindings) -
    # carried forward so Step 31 can enforce "every supplied variable
    # must be consumed by final SQL, else controlled failure."
    variable_names: List[str] = Field(
        default_factory=list
    )

    message: Optional[str] = None
    error_code: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 31 - deterministic Python SQL rendering of the Step-30
# SecondaryQueryPlan. No LLM, no value binding/substitution - the
# reference itself never substitutes variable values at this stage,
# only verifies the exact `{variable_name}` placeholder token is
# present in the rendered SQL text.
# ================================================================


class SecondaryDeterministicSQLResult(ContractModel):
    """
    Step-31 typed output. On success, `sql` is deterministic generated
    SQL text (still containing literal `{variable_name}` placeholders
    - no values are substituted here) and `unconsumed_variables` is
    always []. On failure, `sql` is never populated as trusted/
    executable output.

    STEP-32 TRUST BOUNDARY: this is the ONLY object Step 32 may
    consume for SQL validation - never WorkflowResult diagnostics,
    never a re-rendered/regenerated SQL string.
    """

    success: bool

    report_name: str
    report_key: str

    final_concepts: List[str] = Field(
        default_factory=list
    )

    winning_rule: Optional[str] = None
    winning_case_id: Optional[str] = None
    query_shape: Optional[str] = None

    sql: Optional[str] = None

    consumed_variables: List[str] = Field(
        default_factory=list
    )
    unconsumed_variables: List[str] = Field(
        default_factory=list
    )

    message: Optional[str] = None
    error_code: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 32 - static deterministic validation of the Step-31 executable
# SQL, using the SAME existing Primary preparation/validation services
# (apply_extra_sql_filters / resolve_variable_bindings /
# inject_sql_values / validate_sql). No LLM, no Oracle, no execution.
# ================================================================


class SecondarySQLValidationResult(ContractModel):
    """
    Step-32 typed output. On success, `executable_sql` is safe to hand
    to Step 33 semantic validation - still no Oracle schema check, no
    execution. On failure, `executable_sql` is never populated as
    trusted/approved output.

    STEP-33 TRUST BOUNDARY: this is the ONLY object Step 33 may
    consume for its SQL input - never WorkflowResult diagnostics,
    never a re-prepared/re-validated SQL string.

    STEP-34 TRUST BOUNDARY: `static_validation_result` is the SAME
    typed `SQLValidationResult` object the ONE `validate_sql()` call
    below actually returned (never a second call, never rebuilt from
    `static_validation`) - this is the ONLY object Step 34 may pass to
    `validate_sql_against_oracle_schema()`, mirroring exactly how
    Primary keeps its own single `validate_sql()` result in a local
    variable and passes it straight through to Oracle schema
    validation.
    """

    success: bool

    report_name: str
    report_key: str
    query_shape: Optional[str] = None

    sql_template: Optional[str] = None
    executable_sql: Optional[str] = None

    allowed_tables: List[str] = Field(
        default_factory=list
    )

    variable_bindings: Dict[str, Any] = Field(
        default_factory=dict
    )
    consumed_variables: List[str] = Field(
        default_factory=list
    )

    # Authoritative typed internal handoff (Step 34 trust boundary -
    # see class docstring). Not part of the outward diagnostics
    # contract.
    static_validation_result: Optional[
        SQLValidationResult
    ] = None

    # Serialized diagnostics/API-compatibility view of the SAME
    # object above - outward-facing only, never re-parsed back into a
    # typed SQLValidationResult by any downstream step.
    static_validation: Dict[str, Any] = Field(
        default_factory=dict
    )
    issues: List[Dict[str, Any]] = Field(
        default_factory=list
    )

    error_code: Optional[str] = None
    message: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 33 - LLM semantic SQL validation + bounded, Python-only
# QueryPlan correction.
#
# `SecondaryQueryPlanCorrectionRequest` and `SecondarySemanticSQLAssessment`
# are STRICT (extra="forbid") plain BaseModels - deliberately NOT
# ContractModel (which permissively allows extra fields). This is the
# raw structured-output contract for the checker LLM: any attempt by
# the model to smuggle in a `sql`/`corrected_sql`/`sql_patch` field (or
# anything else not explicitly declared here) fails Pydantic
# validation instead of being silently accepted, which is exactly how
# "the LLM cannot return SQL" is enforced at the schema level, not by
# convention.
# ================================================================


# Stable, generic reason-code vocabulary the CHECKER LLM itself may
# choose from. Deliberately excludes "checker_invalid_output" and
# "checker_failure" - those are assigned by backend_v4's OWN code when
# the checker's output/call itself failed technically, never something
# the model chooses for itself.
SECONDARY_SEMANTIC_REASON_CODES = frozenset(
    {
        "semantic_match",
        "missing_requested_field",
        "wrong_aggregation",
        "wrong_grouping",
        "wrong_granularity",
        "missing_filter",
        "incorrect_filter_semantics",
        "wrong_ordering",
        "wrong_limit",
        "wrong_distinct_behavior",
        "variable_semantic_mismatch",
        "query_shape_mismatch",
        "winning_case_semantic_mismatch",
        "unsupported_correction",
    }
)

# The ONLY correction types Python can safely apply without inventing
# any table/column/variable/concept/rule/case identifier. Deliberately
# small - see secondary/query_plan_correction_service.py.
SECONDARY_SUPPORTED_CORRECTION_TYPES = frozenset(
    {"set_limit", "set_distinct"}
)

# Reason codes that, by architecture, must NEVER carry a correction
# request - the winning query case can never be reselected here
# (winning_case_semantic_mismatch), and a code that literally means
# "no supported correction applies" is self-contradictory if paired
# with one (unsupported_correction). "checker_failure" is
# deliberately NOT listed here - it is a backend/result-level code
# only, never a value the LLM-facing reason_code field can hold (see
# SECONDARY_SEMANTIC_REASON_CODES above), so the invariant already
# holds structurally for it.
SECONDARY_NO_CORRECTION_REASON_CODES = frozenset(
    {
        "winning_case_semantic_mismatch",
        "unsupported_correction",
    }
)


class SecondaryQueryPlanCorrectionRequest(BaseModel):
    """
    Raw structured correction request from the checker LLM. This is
    NEVER applied directly - secondary/query_plan_correction_service.py
    is the only code that may turn this into an actual QueryPlan edit,
    after validating `correction_type` is one of
    SECONDARY_SUPPORTED_CORRECTION_TYPES and the supplied value is
    structurally valid for the target plan_spec shape.
    """

    model_config = ConfigDict(extra="forbid")

    correction_type: str
    limit: Optional[int] = None
    distinct: Optional[bool] = None


class SecondarySemanticSQLAssessment(BaseModel):
    """
    Strict structured output of the Step-33 semantic checker LLM call.
    No `sql`/`corrected_sql`/`sql_patch`/free-form SQL field exists on
    this model, and `extra="forbid"` rejects any attempt by the model
    to add one - this is the schema-level proof (not merely a prompt
    instruction) that the checker cannot return SQL.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool
    reason_code: str
    summary: str = Field(..., min_length=1, max_length=500)
    correction_required: bool = False
    correction: Optional[
        SecondaryQueryPlanCorrectionRequest
    ] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("reason_code")
    @classmethod
    def _validate_reason_code(cls, value: str) -> str:
        if value not in SECONDARY_SEMANTIC_REASON_CODES:
            raise ValueError(
                "Unsupported semantic reason_code: "
                f"{value!r}."
            )
        return value

    @model_validator(mode="after")
    def _check_consistency(
        self,
    ) -> "SecondarySemanticSQLAssessment":
        """
        Deterministic cross-field consistency, enforced in BOTH
        directions - not merely "a valid result can't also request a
        correction" but the full biconditional contract:

            valid == True  <=>  reason_code == "semantic_match"
            correction_required == True
                => valid == False and correction is not None
            correction_required == False => correction is None
            reason_code in SECONDARY_NO_CORRECTION_REASON_CODES
                => correction_required == False and correction is None

        Any violation raises ValueError here, which
        secondary/semantic_sql_checker.py's model_validate_json() call
        already converts into a retryable
        SecondarySemanticSQLCheckerError - an internally contradictory
        assessment (e.g. valid=True with a non-semantic_match
        reason_code) is therefore never accepted as a successful
        result; it is handled identically to any other malformed
        structured checker output.
        """

        if (
            self.valid
            and self.reason_code != "semantic_match"
        ):
            raise ValueError(
                "valid=True requires reason_code == "
                "'semantic_match'."
            )

        if (
            not self.valid
            and self.reason_code == "semantic_match"
        ):
            raise ValueError(
                "reason_code == 'semantic_match' requires "
                "valid=True."
            )

        if self.correction_required:
            if self.valid:
                raise ValueError(
                    "correction_required=True requires "
                    "valid=False."
                )

            if self.correction is None:
                raise ValueError(
                    "correction_required=True requires a "
                    "correction object."
                )

        elif self.correction is not None:
            raise ValueError(
                "correction_required=False must not carry "
                "a correction object."
            )

        if (
            self.reason_code
            in SECONDARY_NO_CORRECTION_REASON_CODES
            and (
                self.correction_required
                or self.correction is not None
            )
        ):
            raise ValueError(
                f"reason_code {self.reason_code!r} must "
                "never request a correction."
            )

        return self


class SecondaryCorrectionAttempt(ContractModel):
    """
    One tracked Step-33 QueryPlan-correction cycle. Checker technical
    retries are NOT tracked here - only actual correction attempts
    consume the bounded correction budget.
    """

    attempt_number: int

    reason_code: Optional[str] = None
    correction_type: Optional[str] = None

    correction_applied: bool = False

    outcome: str
    message: Optional[str] = None


class SecondarySemanticSQLValidationResult(ContractModel):
    """
    Step-33 typed output. On success, `final_query_plan`/
    `final_sql_validation` are the authoritative (possibly corrected)
    typed objects Step 34 must consume directly.

    STEP-34 INPUT CONTRACT: Step 34 must consume this result directly
    - never WorkflowResult diagnostics, never a rebuilt SQL string.
    """

    success: bool

    report_name: str
    report_key: str

    semantic_valid: bool = False
    reason_code: Optional[str] = None
    summary: Optional[str] = None

    final_query_plan: Optional[SecondaryQueryPlan] = None
    final_sql_validation: Optional[
        SecondarySQLValidationResult
    ] = None

    correction_attempts: List[
        SecondaryCorrectionAttempt
    ] = Field(default_factory=list)
    correction_budget_max: int = 2

    error_code: Optional[str] = None
    message: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


# ================================================================
# STEP 34 - Oracle physical schema validation of the FINAL Step-33
# executable SQL, reusing the SAME existing
# services.oracle_schema_validator.validate_sql_against_oracle_schema
# Primary already uses. No duplicate Oracle validator, no LLM, no
# business-query execution.
# ================================================================


class SecondaryOracleSchemaValidationResult(ContractModel):
    """
    Step-34 typed output. `final_query_plan`/`final_sql_validation`
    are carried forward verbatim from the authoritative successful
    Step-33 `SecondarySemanticSQLValidationResult` - this is the ONLY
    object Step 34 may consume for its SQL/QueryPlan input, never
    WorkflowResult diagnostics, never a re-rendered SQL string.

    On success, `schema_valid == True` and the result is eligible for
    Step 35. On failure, no SQL is approved for execution.
    """

    success: bool

    report_name: str
    report_key: str
    query_shape: Optional[str] = None

    final_query_plan: Optional[SecondaryQueryPlan] = None
    final_sql_validation: Optional[
        SecondarySQLValidationResult
    ] = None

    executable_sql: Optional[str] = None

    schema_valid: bool = False

    oracle_schema_validation: Optional[
        OracleSchemaValidationResult
    ] = None

    issues: List[
        OracleSchemaValidationIssue
    ] = Field(default_factory=list)

    error_code: Optional[str] = None
    message: Optional[str] = None

    # Deterministic, evidence-based routing hint only - Step 34 never
    # acts on this itself (no automatic YAML edit, no case reselection,
    # no unrestricted Step-33 correction loop). Conceptually either
    # "metadata_configuration_mismatch" (Oracle authoritatively proved
    # a configured table/column is physically invalid) or
    # "technical_schema_validation_failure" (the schema-validation
    # infrastructure itself could not complete).
    recommended_action: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryResolvedRule(ContractModel):
    name: str
    description: str = ""

    status: SecondaryRuleStatus
    selected: bool = False

    priority: int = 100

    mandatory: bool = False
    auto_apply: bool = False

    activation_sources: List[str] = Field(
        default_factory=list
    )

    selection_reasons: List[str] = Field(
        default_factory=list
    )

    applies_when_mode: str = "all"

    required_concepts: List[str] = Field(
        default_factory=list
    )

    matched_concepts: List[str] = Field(
        default_factory=list
    )

    missing_concepts: List[str] = Field(
        default_factory=list
    )

    tables: List[str] = Field(
        default_factory=list
    )

    columns: List[str] = Field(
        default_factory=list
    )

    filters: List[Any] = Field(
        default_factory=list
    )

    calculations: List[Any] = Field(
        default_factory=list
    )

    group_by: List[str] = Field(
        default_factory=list
    )

    order_by: List[Any] = Field(
        default_factory=list
    )

    joins: List[Any] = Field(
        default_factory=list
    )

    constraints: List[Any] = Field(
        default_factory=list
    )

    conflicts_with: List[str] = Field(
        default_factory=list
    )

    enforces: Dict[str, Any] = Field(
        default_factory=dict
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryRuleConflict(ContractModel):
    rule_names: List[str]

    message: str

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryRuleResolutionResult(
    ContractModel
):
    success: bool
    complete: bool

    detected_concepts: List[str] = Field(
        default_factory=list
    )

    activated_rule_names: List[str] = Field(
        default_factory=list
    )

    selected_rule_names: List[str] = Field(
        default_factory=list
    )

    selected_rules: List[
        SecondaryResolvedRule
    ] = Field(
        default_factory=list
    )

    blocked_rules: List[
        SecondaryResolvedRule
    ] = Field(
        default_factory=list
    )

    eligible_rules: List[
        SecondaryResolvedRule
    ] = Field(
        default_factory=list
    )

    conflicting_rules: List[
        SecondaryResolvedRule
    ] = Field(
        default_factory=list
    )

    conflicts: List[
        SecondaryRuleConflict
    ] = Field(
        default_factory=list
    )

    effective_tables: List[str] = Field(
        default_factory=list
    )

    effective_columns: List[str] = Field(
        default_factory=list
    )

    effective_filters: List[Any] = Field(
        default_factory=list
    )

    effective_calculations: List[Any] = Field(
        default_factory=list
    )

    effective_group_by: List[str] = Field(
        default_factory=list
    )

    effective_order_by: List[Any] = Field(
        default_factory=list
    )

    effective_joins: List[Any] = Field(
        default_factory=list
    )

    effective_constraints: List[Any] = Field(
        default_factory=list
    )

    requires_clarification: bool = False

    warnings: List[str] = Field(
        default_factory=list
    )

    message: str

    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondaryVerifiedTable(
    ContractModel
):
    module: str

    schema_name: Optional[str] = None
    table_name: str
    qualified_table_name: str

    source_rules: List[str] = Field(
        default_factory=list
    )

    metadata_column_count: int = 0


class SecondaryVerifiedColumn(
    ContractModel
):
    module: str

    schema_name: Optional[str] = None
    table_name: str
    qualified_table_name: str

    column_name: str

    data_type: Optional[str] = None
    description: Optional[str] = None
    filterable: bool = False

    metadata_uuid: Optional[str] = None
    metadata_custom_id: Optional[str] = None

    source_rules: List[str] = Field(
        default_factory=list
    )


class SecondaryContextIssue(
    ContractModel
):
    severity: SecondaryContextIssueSeverity

    code: str
    message: str

    rule_name: Optional[str] = None
    table_name: Optional[str] = None
    column_name: Optional[str] = None

    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class SecondarySQLGenerationContext(
    ContractModel
):
    report_name: str
    module: str

    schema_name: Optional[str] = None

    normalized_question: str

    variable_names: List[str] = Field(
        default_factory=list
    )

    variable_placeholders: List[str] = Field(
        default_factory=list
    )

    detected_concepts: List[str] = Field(
        default_factory=list
    )

    selected_rule_names: List[str] = Field(
        default_factory=list
    )

    selected_rules: List[
        SecondaryResolvedRule
    ] = Field(
        default_factory=list
    )

    verified_tables: List[
        SecondaryVerifiedTable
    ] = Field(
        default_factory=list
    )

    verified_columns: List[
        SecondaryVerifiedColumn
    ] = Field(
        default_factory=list
    )

    allowed_tables: List[str] = Field(
        default_factory=list
    )

    allowed_columns: Dict[
        str,
        List[str],
    ] = Field(
        default_factory=dict
    )

    required_filters: List[Any] = Field(
        default_factory=list
    )

    required_calculations: List[Any] = Field(
        default_factory=list
    )

    required_group_by: List[str] = Field(
        default_factory=list
    )

    required_order_by: List[Any] = Field(
        default_factory=list
    )

    required_joins: List[Any] = Field(
        default_factory=list
    )

    required_constraints: List[Any] = Field(
        default_factory=list
    )

    generation_instructions: List[str] = Field(
        default_factory=list
    )


class SecondaryContextBuildResult(
    ContractModel
):
    success: bool
    complete: bool

    context: Optional[
        SecondarySQLGenerationContext
    ] = None

    issues: List[
        SecondaryContextIssue
    ] = Field(
        default_factory=list
    )

    requires_clarification: bool = False

    message: str

    error_code: Optional[str] = None
    exception_type: Optional[str] = None

    diagnostics: Dict[str, Any] = Field(
        default_factory=dict
    )



class PrimaryExecutionAttempt(ContractModel):
    """
    One Oracle execution attempt made by the primary workflow.
    """

    attempt_number: int
    sql_hash: str

    execution_result: SQLExecutionResult

    oracle_classification: Optional[
        OracleErrorClassification
    ] = None

    retry_scheduled: bool = False
    retry_delay_seconds: float = 0.0


class PrimaryExecutionRetryResult(ContractModel):
    """
    Result of bounded execution-only retry handling.
    """

    success: bool

    final_result: SQLExecutionResult

    attempt_count: int
    exhausted: bool = False

    # Step 21: distinct from `exhausted` (Oracle same-SQL transient
    # retry budget exhausted). This means the request-level DB
    # execution safety cap was reached and the loop stopped BEFORE
    # calling the executor again - not an Oracle retry outcome.
    db_cap_exhausted: bool = False

    attempts: List[
        PrimaryExecutionAttempt
    ] = Field(
        default_factory=list
    )


class SecondaryBusinessExecutionResult(ContractModel):
    """
    STEP 35: outcome of the ONE shared business-SQL execution attempt
    (including bounded same-SQL transient retries) for the FINAL
    Step-34 Oracle-schema-valid Secondary executable SQL.

    Produced by
    secondary.business_execution_orchestrator.
    SecondaryBusinessExecutionOrchestrator, which calls the SAME
    workflow-agnostic
    services.sql_execution_retry_service.execute_sql_with_retry(...)
    Primary itself now uses - no Secondary-specific execution engine,
    no Secondary-specific Oracle classifier. `execution_retry` is the
    verbatim typed result of that ONE call.
    """

    success: bool

    report_name: str
    report_key: str
    query_shape: Optional[str] = None

    executable_sql: Optional[str] = None

    query_executed: bool = False
    results: List[Dict[str, Any]] = Field(
        default_factory=list
    )
    result_count: int = 0
    row_limit: int = 50

    execution_retry: Optional[
        PrimaryExecutionRetryResult
    ] = None

    error_kind: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


class FeedbackExecutionContext(ContractModel):
    """
    Deterministically reconstructed previous execution state for the
    feedback intent.

    This is historical context only: nothing here executes SQL,
    calls an LLM, or merges/decides anything. A later Feedback Delta
    Resolution step consumes it.

    Every field's provenance is documented below. Several fields are
    NOT currently persisted anywhere in the backend; those are left
    Optional and are always None/empty today rather than fabricated.
    """

    # Identity of the reconstructed previous execution.
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    source_user_message_id: Optional[str] = None

    # Only populated for Primary-routed workflows (data_query /
    # feedback re-entering Primary). value_lookup and metadata_query
    # do not create a Mongo workflow record, so this is None there.
    workflow_id: Optional[str] = None

    session_id: Optional[str] = None

    # The original user request that produced this previous
    # execution, retrieved via the execution's stored
    # source_user_message_id. None if that reference is absent, the
    # referenced message no longer exists, or it does not belong to
    # this same conversation.
    original_user_input: Optional[str] = None

    # Stored normalized request when available. For feedback-generated
    # executions this comes from resolved_normalized_input; otherwise it
    # comes from the source user message's normalized_input.
    resolved_question: Optional[str] = None

    # NOT currently persisted on chat messages (no "intent" field is
    # stored). Always None today.
    intent: Optional[str] = None

    # The stored workflow_used value, e.g. "primary",
    # "value_lookup", or "secondary".
    workflow: Optional[str] = None

    # The PREVIOUS report/context, distinct from whatever report the
    # current request happens to have selected.
    report_name: Optional[str] = None

    # All previously stored variables, verbatim, preserving
    # name/value/source structure.
    variables: List[Dict[str, Any]] = Field(
        default_factory=list,
    )

    # Subset of `variables` where source == "option" (sidebar/extra
    # variables), derived by filtering already-stored data - not a
    # separately persisted field.
    extra_variables: List[Dict[str, Any]] = Field(
        default_factory=list,
    )

    # Stored verbatim. None for workflows that do not persist SQL
    # (value_lookup, metadata_query).
    sql: Optional[str] = None

    # NOT currently persisted as an explicit flag on chat messages.
    # Always None today.
    query_executed: Optional[bool] = None

    # Derived from result_rows[0].keys() when rows exist. Not itself
    # a persisted field.
    result_columns: List[str] = Field(
        default_factory=list,
    )

    # Stored table_rows. IMPORTANT: this is the outward,
    # adequacy-filtered view (compute_outward_results), not
    # necessarily every row Primary/Oracle originally returned.
    result_rows: List[Dict[str, Any]] = Field(
        default_factory=list,
    )

    # Stored result_count.
    row_count: int = 0

    # NOT currently persisted; no truncation flag is stored anywhere.
    # Always None today.
    result_truncated: Optional[bool] = None

    # NOT currently persisted on chat messages (no diagnostics/
    # metadata dict is stored). Always None today.
    result_metadata: Optional[Dict[str, Any]] = None

    # NOT currently persisted on chat messages. Always None today.
    workflow_diagnostics: Optional[Dict[str, Any]] = None

    # The stored assistant response text - the closest available
    # equivalent to a separate "result analysis" field, since no
    # distinct analysis field is persisted.
    previous_assistant_message: Optional[str] = None

    # Stored workflow status/stage (e.g. "success", "no_data").
    status: Optional[str] = None
    stage: Optional[str] = None

    created_at: Optional[str] = None

    # "explicit_reply" or "latest_reusable" - how this context was
    # selected. See FeedbackContextService.
    selection_source: Optional[str] = None


class FeedbackVariableOperation(ContractModel):
    """
    One deterministic change to a single structured variable,
    produced (and validated) by FeedbackDeltaService.

    ADD: a variable not present previously.
    REPLACE: an existing variable's value changes.
    REMOVE: an existing variable is explicitly dropped.
    RETAIN: the user explicitly says an existing variable/filter
    should remain - this is informational only; unmentioned previous
    variables already remain unchanged by default without needing an
    explicit RETAIN operation.
    """

    operation: FeedbackOperationType

    name: str

    old_value: Optional[Any] = None
    new_value: Optional[Any] = None

    source: Optional[str] = None


class FeedbackRequestOperation(ContractModel):
    """
    One deterministic change to a generic (non-variable) request
    requirement - a filter, requested output, grouping, ordering,
    limit, or other condition expressed in natural language rather
    than as a structured frontend variable.

    The category vocabulary is behavioral, not tied to any specific
    database schema.
    """

    operation: FeedbackOperationType
    category: FeedbackRequestCategory

    target: Optional[str] = None
    previous_value: Optional[Any] = None
    new_value: Optional[Any] = None


class FeedbackReportChange(ContractModel):
    """
    Whether the feedback message itself explicitly changes the
    report/context, independent of whatever report the frontend
    currently happens to have selected.
    """

    changed: bool = False
    new_report_name: Optional[str] = None


class FeedbackDelta(ContractModel):
    """
    Structured, validated representation of what a feedback message
    changes about the previous request. Produced by
    FeedbackDeltaService after deterministic post-LLM validation -
    never the raw, untrusted model output.
    """

    report_change: FeedbackReportChange = Field(
        default_factory=FeedbackReportChange,
    )

    variable_operations: List[
        FeedbackVariableOperation
    ] = Field(
        default_factory=list,
    )

    request_operations: List[
        FeedbackRequestOperation
    ] = Field(
        default_factory=list,
    )

    retain_previous_constraints: bool = True

    clarification_required: bool = False
    clarification_reason: Optional[str] = None

    confidence: Optional[float] = None
    reasoning_summary: Optional[str] = None


class ResolvedFeedbackRequest(ContractModel):
    """
    "This is what the user now means" - not "this is how we will
    execute it." The next roadmap step (existing-result vs
    database-rerun decision) consumes this; it is not decided here.
    """

    previous_context: FeedbackExecutionContext
    delta: FeedbackDelta

    standalone_question: Optional[str] = None

    effective_report_name: Optional[str] = None

    effective_variables: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list,
    )

    effective_extra_variables: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list,
    )

    current_feedback_input: str

    requires_clarification: bool = False
    clarification_reason: Optional[str] = None


class FeedbackExecutionDecision(ContractModel):
    """
    Which route a resolved feedback request should take next.
    Produced by FeedbackExecutionDecisionService. The chosen route is
    NOT executed by this model or by that service - a later roadmap
    step performs it.

    The three boolean flags are structurally guaranteed to agree
    with `route`; it is impossible to construct an internally
    inconsistent decision.
    """

    route: FeedbackExecutionRoute

    reason_code: str
    reason_summary: str

    can_use_existing_result: bool
    requires_database_reexecution: bool
    requires_clarification: bool

    # Whether ANY previous result state (including a genuine,
    # confirmed zero-row result) is available to reason about at
    # all - not just row_count, since a stored zero-row result
    # differs from no result ever having been stored.
    previous_result_available: bool

    # True only when the previous execution explicitly recorded
    # whether its result was truncated. Currently this is never
    # persisted, so this is False in the live system today -
    # result_complete must then stay None (unknown), never assumed
    # True.
    result_completeness_known: bool
    result_complete: Optional[bool] = None

    required_columns: List[str] = Field(
        default_factory=list,
    )
    available_columns: List[str] = Field(
        default_factory=list,
    )
    missing_columns: List[str] = Field(
        default_factory=list,
    )

    decision_factors: List[str] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def _check_route_consistency(
        self,
    ) -> "FeedbackExecutionDecision":

        expected = {
            FeedbackExecutionRoute
            .EXISTING_RESULT_ONLY: (
                True,
                False,
                False,
            ),
            FeedbackExecutionRoute
            .DATABASE_REEXECUTION_REQUIRED: (
                False,
                True,
                False,
            ),
            FeedbackExecutionRoute
            .CLARIFICATION_REQUIRED: (
                False,
                False,
                True,
            ),
        }[self.route]

        actual = (
            self.can_use_existing_result,
            self.requires_database_reexecution,
            self.requires_clarification,
        )

        if actual != expected:

            raise ValueError(
                "FeedbackExecutionDecision boolean flags "
                f"{actual} are inconsistent with route "
                f"{self.route!r}; expected {expected}."
            )

        return self


class MetadataGovernanceIssue(ContractModel):
    """
    One deterministic Step 38 governance finding produced by
    MetadataGovernanceService. Always a reproducible comparison
    result against an authoritative or physical source - never
    generated by an LLM or invented from a technical failure.
    """

    code: MetadataGovernanceIssueCode
    severity: MetadataGovernanceSeverity
    source_kind: str

    entity_type: str
    entity_id: Optional[str] = None
    report_name: Optional[str] = None

    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class MetadataSourceGovernanceStatus(ContractModel):
    """
    Governance snapshot for ONE metadata source (report registry,
    ConceptCatalog, ColumnMetadata, Oracle physical schema, variable
    mapping). `fresh=None` means freshness is unknown rather than
    current - a source without real version/hash provenance must
    never be reported as fresh.
    """

    source_kind: str
    authoritative: bool

    available: bool
    valid: bool
    fresh: Optional[bool] = None

    item_count: int = 0
    source_version: Optional[str] = None

    issues: List[MetadataGovernanceIssue] = Field(
        default_factory=list,
    )


class MetadataGovernanceAuditResult(ContractModel):
    """
    Read-only Step 38 governance audit output. Produced by
    MetadataGovernanceService.audit(); never mutates any metadata
    source itself - repair remains a separate, explicitly-invoked
    operation (e.g. the existing ConceptCatalog sync path).
    """

    success: bool
    healthy: bool

    sources: List[MetadataSourceGovernanceStatus] = Field(
        default_factory=list,
    )
    issues: List[MetadataGovernanceIssue] = Field(
        default_factory=list,
    )

    error_code: Optional[str] = None
    message: str = ""

    diagnostics: Dict[str, Any] = Field(default_factory=dict)




