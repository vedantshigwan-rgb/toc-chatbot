from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)


def _json_safe_value(
    value: Any,
) -> Any:
    if value is None:
        return None

    if isinstance(
        value,
        Decimal,
    ):
        return float(value)

    if isinstance(
        value,
        (date, datetime),
    ):
        return value.isoformat()

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    return str(value)


def _build_result_profile(
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a compact deterministic profile of the SQL result.

    We calculate statistics in Python so the LLM does not
    need to derive everything from raw database rows.
    """

    if not rows:
        return {
            "row_count": 0,
            "columns": [],
            "column_profiles": {},
            "sample_rows": [],
        }

    columns = []

    seen_columns = set()

    for row in rows:
        for column in row.keys():
            if column not in seen_columns:
                seen_columns.add(column)
                columns.append(column)

    column_profiles = {}

    for column in columns:
        values = [
            row.get(column)
            for row in rows
        ]

        non_null_values = [
            value
            for value in values
            if value is not None
        ]

        profile = {
            "null_count":
                len(values) -
                len(non_null_values),

            "non_null_count":
                len(non_null_values),
        }

        numeric_values = []

        for value in non_null_values:
            if (
                isinstance(
                    value,
                    (int, float, Decimal),
                )
                and not isinstance(
                    value,
                    bool,
                )
            ):
                numeric_values.append(
                    float(value)
                )

        if (
            numeric_values
            and
            len(numeric_values)
            ==
            len(non_null_values)
        ):
            profile.update(
                {
                    "type": "numeric",

                    "min":
                        min(
                            numeric_values
                        ),

                    "max":
                        max(
                            numeric_values
                        ),

                    "sum":
                        sum(
                            numeric_values
                        ),

                    "average":
                        (
                            sum(
                                numeric_values
                            )
                            /
                            len(
                                numeric_values
                            )
                        ),
                }
            )

        else:
            distinct_values = []

            seen_values = set()

            for value in non_null_values:
                safe_value = str(
                    _json_safe_value(
                        value
                    )
                )

                if (
                    safe_value
                    not in seen_values
                ):
                    seen_values.add(
                        safe_value
                    )

                    distinct_values.append(
                        safe_value
                    )

                if (
                    len(
                        distinct_values
                    )
                    >= 10
                ):
                    break

            profile.update(
                {
                    "type": "text",

                    "sample_distinct_values":
                        distinct_values,
                }
            )

        column_profiles[column] = (
            profile
        )

    sample_rows = []

    for row in rows[:10]:
        sample_rows.append(
            {
                key:
                    _json_safe_value(
                        value
                    )
                for key, value
                in row.items()
            }
        )

    return {
        "row_count":
            len(rows),

        "columns":
            columns,

        "column_profiles":
            column_profiles,

        "sample_rows":
            sample_rows,
    }


async def generate_result_analysis(
    *,
    user_input: str,
    report_name: str,
    rows: List[Dict[str, Any]],
    client,
    semaphore,
    model: str = "gpt-5-mini",
) -> str:
    """
    Generate a short grounded explanation of SQL results.

    Important:
    - SQL result rows remain the source of truth.
    - The LLM receives deterministic statistics plus a bounded
      sample instead of an unlimited raw result set.
    """

    if not rows:
        return (
            "The query executed successfully, "
            "but no rows were returned."
        )

    profile = (
        _build_result_profile(
            rows
        )
    )

    system_prompt = """
    You are the conversational data analyst for an enterprise reporting assistant.

    The user asked a business question and a query has already been executed successfully.
    Your job is to answer the user's question naturally using the supplied data profile.

    IMPORTANT RULES:

    1. Speak directly to the user like a business reporting assistant.
    2. Do NOT use technical phrases such as:
    - "returned data"
    - "returned rows"
    - "SQL result"
    - "SQL query"
    - "result profile"
    - "database"
    - "dataset"
    - "column profile"
    3. Do NOT explain how the data was retrieved.
    4. Do NOT say things such as:
    - "These figures are taken directly from..."
    - "According to the SQL result..."
    - "The returned data contains..."
    5. Answer the actual business question first.
    6. Mention the most useful totals, counts, values or observations.
    7. Do not dump statistics just because they are available.
    8. Do not mention min, max, average or sum unless they add useful meaning to the user's question.
    9. Format large numbers naturally with commas.
    10. Avoid unnecessary decimal precision.
    11. If a number is extremely large, you may also express it naturally using million/billion where useful.
    12. Do not invent units or currency symbols when they are not explicitly known.
    13. Do not make business conclusions that are not supported by the supplied information.
    14. Keep the answer concise: normally 2 to 4 sentences.
    15. End naturally by indicating that the detailed breakdown is available in the table only when useful.
    16. Do not list every record because the user can open the detailed table separately.

    GOOD STYLE EXAMPLE:

    "Inventory is available for 43 buyers, covering 137,655 items. The combined stock count is approximately 14.78 million, with a total stock value of about 7.30 billion. You can view the buyer-wise breakdown in the table."

    BAD STYLE EXAMPLE:

    "Returned data: 43 buyers with columns BUYER_NAME, TOTAL_ITEMS...
    Summary statistics from the returned rows...
    These figures were taken directly from the SQL result."

    Return plain text only.
    """.strip()

    user_prompt = f"""
    User's question:
    {user_input}

    Report:
    {report_name}

    Information available for answering the question:
    {json.dumps(
        profile,
        ensure_ascii=False,
        default=str,
    )}

    Answer the user's question naturally and concisely.
    """.strip()

    async with semaphore:
        response = (
            await client.responses.create(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
            )
        )

    output_text = str(
        getattr(
            response,
            "output_text",
            "",
        )
        or ""
    ).strip()

    if not output_text:
        raise ValueError(
            "The result analysis model "
            "returned an empty response."
        )

    return output_text




# =============================================================================
# FOLLOW-UP RESULT ANALYSIS
# =============================================================================


class ResultAnalysisServiceError(
    RuntimeError
):
    """
    Raised when an existing stored result cannot
    be analysed for a follow-up question.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:

        super().__init__(message)

        self.code = code
        self.details = details or {}


class ResultAnalysisOutcome(str, Enum):
    """
    Discriminates the three shapes of result WorkflowManager must
    map to different outward actions.
    """

    ANALYZED = "analyzed"
    DATABASE_DATA_REQUIRED = "database_data_required"
    CLARIFICATION_REQUIRED = "clarification_required"


class ResultAnalysisOutput(BaseModel):
    """
    Result returned to WorkflowManager after analysing
    an already-stored result.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    outcome: ResultAnalysisOutcome = (
        ResultAnalysisOutcome.ANALYZED
    )

    message: str

    rows_available: int

    rows_used: int

    truncated: bool

    operation: Optional[str] = None

    reason_code: Optional[str] = None

    # Only populated for operations that produce a new row set
    # (sort/filter/rank/top_n/bottom_n/select_columns/limit) so the
    # caller can re-render an updated table. None for count/aggregate/
    # summarize/explain/compare, which only produce a message.
    result_rows: Optional[
        List[Dict[str, Any]]
    ] = None


# =============================================================================
# DETERMINISTIC OPERATION CONTRACT
# =============================================================================
#
# An LLM is used ONLY to classify which of these operations the user
# is asking for and to extract its parameters (column names, a
# limit/N, a filter value). It never computes the sorted order, the
# count, or the aggregate itself - that is always plain Python below.


class ResultAnalysisOperation(str, Enum):
    SUMMARIZE = "summarize"
    EXPLAIN = "explain"
    COMPARE = "compare"
    SORT = "sort"
    RANK = "rank"
    TOP_N = "top_n"
    BOTTOM_N = "bottom_n"
    FILTER = "filter"
    COUNT = "count"
    AGGREGATE = "aggregate"
    SELECT_COLUMNS = "select_columns"
    LIMIT = "limit"
    OTHER = "other"


class ResultAnalysisAggregateFunction(str, Enum):
    SUM = "sum"
    AVERAGE = "average"
    MIN = "min"
    MAX = "max"


class ResultAnalysisScope(str, Enum):
    """
    RETURNED_RESULT: the question concerns only the rows already
    shown/returned.

    FULL_DATASET: the question requires a conclusion about the
    complete underlying dataset. Since result_truncated is never
    persisted today, completeness is always unknown, so this scope
    always routes to DATABASE_DATA_REQUIRED rather than guessing.
    """

    RETURNED_RESULT = "returned_result"
    FULL_DATASET = "full_dataset"


_DETERMINISTIC_OPERATIONS = {
    ResultAnalysisOperation.SORT,
    ResultAnalysisOperation.RANK,
    ResultAnalysisOperation.TOP_N,
    ResultAnalysisOperation.BOTTOM_N,
    ResultAnalysisOperation.FILTER,
    ResultAnalysisOperation.AGGREGATE,
    ResultAnalysisOperation.SELECT_COLUMNS,
    ResultAnalysisOperation.LIMIT,
}

_SEMANTIC_OPERATIONS = {
    ResultAnalysisOperation.SUMMARIZE,
    ResultAnalysisOperation.EXPLAIN,
    ResultAnalysisOperation.COMPARE,
}


class ResultAnalysisPlan(BaseModel):
    """
    Validated, column-checked description of what to do to the
    stored rows. Only ever constructed by
    _validate_and_normalize_plan() - never directly from raw LLM
    output.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ResultAnalysisOperation
    scope: ResultAnalysisScope = (
        ResultAnalysisScope.RETURNED_RESULT
    )

    target_columns: List[str] = Field(
        default_factory=list,
    )
    filter_column: Optional[str] = None
    filter_value: Optional[Any] = None
    sort_column: Optional[str] = None
    sort_descending: bool = True
    n: Optional[int] = None
    aggregate_function: Optional[
        ResultAnalysisAggregateFunction
    ] = None
    aggregate_column: Optional[str] = None

    requires_clarification: bool = False
    clarification_reason: Optional[str] = None


class _RawResultAnalysisPlan(BaseModel):
    """
    Untrusted, strictly-validated LLM output. Never used directly -
    always passed through _validate_and_normalize_plan() first.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ResultAnalysisOperation
    scope: ResultAnalysisScope = (
        ResultAnalysisScope.RETURNED_RESULT
    )

    target_columns: List[str] = Field(
        default_factory=list,
    )
    filter_column: Optional[str] = None
    filter_value: Optional[Any] = None
    sort_column: Optional[str] = None
    sort_descending: bool = True
    n: Optional[int] = None
    aggregate_function: Optional[
        ResultAnalysisAggregateFunction
    ] = None
    aggregate_column: Optional[str] = None

    clarification_required: bool = False
    clarification_reason: Optional[str] = None


_OPERATION_CLASSIFIER_SYSTEM_PROMPT = """
You are the follow-up analysis operation classifier for a
general-purpose data assistant.

The user is asking a follow-up question about a table of results
that was already returned earlier in the conversation. You do not
have access to the underlying database - only to the column names
of the already-returned rows.

Your ONLY job is to classify what kind of analysis operation the
user wants performed on that already-returned table, and extract the
structured parameters needed to perform it deterministically.

You receive the current follow-up question and the exact list of
column names available in the previously returned table.

OPERATIONS (choose exactly one):

- summarize: a general natural-language summary of the results
- explain: an explanation of a value/pattern in the results
- compare: comparing specific values/rows in the results
- sort: reorder the existing rows by a column
- rank: order rows by a column and enumerate their rank
- top_n: the highest N rows by a column
- bottom_n: the lowest N rows by a column
- filter: keep only rows where a column equals a value
- count: how many rows exist
- aggregate: a single numeric summary (sum/average/min/max) of a
  column
- select_columns: show only specific columns
- limit: show only the first N rows
- other: none of the above cleanly applies

SCOPE:

- "returned_result": the question is about the rows already shown/
  returned/displayed - e.g. "in this table", "these results", "the
  rows shown".
- "full_dataset": the question requires a conclusion about the
  complete underlying dataset beyond what was necessarily returned -
  e.g. "in total across everything", "for every record that exists",
  "overall in the system" - when it is not certain the returned rows
  are the complete dataset.

If unsure, prefer "returned_result".

RULES:

1. Only reference column names from the supplied list. Never invent
   a column name.
2. If the request is ambiguous, or needs a column/value that cannot
   be identified from the supplied list, set clarification_required
   to true and give a short clarification_reason.
3. Do not invent numeric results. Never compute sums, counts, sorted
   order, or rankings yourself - only extract the operation and its
   parameters.
4. Return structured JSON only.

OUTPUT

Return ONLY this JSON structure:

{
  "operation": "sort",
  "scope": "returned_result",
  "target_columns": [],
  "filter_column": null,
  "filter_value": null,
  "sort_column": "...",
  "sort_descending": true,
  "n": null,
  "aggregate_function": null,
  "aggregate_column": null,
  "clarification_required": false,
  "clarification_reason": null
}

No Markdown.
No code fences.
No additional fields.
""".strip()


# =============================================================================
# DETERMINISTIC EXECUTION HELPERS (pure Python, no LLM)
# =============================================================================


def _ordered_columns(
    rows: List[Dict[str, Any]],
) -> List[str]:

    columns: List[str] = []
    seen: set = set()

    for row in rows:
        for column in row.keys():
            if column not in seen:
                seen.add(column)
                columns.append(column)

    return columns


def _sort_key(value: Any) -> Any:
    return (value is None, value)


def _sort_rows(
    rows: List[Dict[str, Any]],
    column: str,
    descending: bool,
) -> List[Dict[str, Any]]:

    return sorted(
        rows,
        key=lambda row: _sort_key(row.get(column)),
        reverse=descending,
    )


def _filter_rows(
    rows: List[Dict[str, Any]],
    column: str,
    value: Any,
) -> List[Dict[str, Any]]:

    target = str(value).strip().casefold()

    return [
        row
        for row in rows
        if str(row.get(column, ""))
        .strip()
        .casefold()
        == target
    ]


def _select_columns(
    rows: List[Dict[str, Any]],
    columns: List[str],
) -> List[Dict[str, Any]]:

    return [
        {column: row.get(column) for column in columns}
        for row in rows
    ]


def _numeric_values(
    rows: List[Dict[str, Any]],
    column: str,
) -> List[float]:

    values = []

    for row in rows:
        value = row.get(column)

        if (
            isinstance(value, (int, float, Decimal))
            and not isinstance(value, bool)
        ):
            values.append(float(value))

    return values


def _format_number(value: float) -> str:

    formatted = f"{value:,.2f}"

    if formatted.endswith(".00"):
        formatted = formatted[:-3]

    return formatted


def _format_row_preview(
    rows: List[Dict[str, Any]],
    *,
    columns: Optional[List[str]] = None,
    max_rows: int = 5,
) -> str:

    preview_rows = rows[:max_rows]

    lines = []

    for row in preview_rows:
        keys = columns if columns else list(row.keys())
        lines.append(
            ", ".join(
                f"{key}={_json_safe_value(row.get(key))}"
                for key in keys
            )
        )

    preview = "; ".join(lines)

    remaining = len(rows) - len(preview_rows)

    if remaining > 0:
        preview += (
            f" (+{remaining} more "
            f"row{'s' if remaining != 1 else ''})"
        )

    return preview


def _execute_deterministic_plan(
    plan: ResultAnalysisPlan,
    rows: List[Dict[str, Any]],
) -> Tuple[str, Optional[List[Dict[str, Any]]]]:

    if plan.operation == ResultAnalysisOperation.AGGREGATE:

        values = _numeric_values(
            rows, plan.aggregate_column
        )

        function = plan.aggregate_function

        if function == ResultAnalysisAggregateFunction.SUM:
            result_value = sum(values)
            label = "sum"
        elif (
            function
            == ResultAnalysisAggregateFunction.AVERAGE
        ):
            result_value = sum(values) / len(values)
            label = "average"
        elif function == ResultAnalysisAggregateFunction.MIN:
            result_value = min(values)
            label = "minimum"
        else:
            result_value = max(values)
            label = "maximum"

        message = (
            f"The {label} of {plan.aggregate_column} "
            f"is {_format_number(result_value)}."
        )

        return message, None

    if plan.operation == ResultAnalysisOperation.SORT:

        sorted_rows = _sort_rows(
            rows, plan.sort_column, plan.sort_descending
        )

        direction = (
            "descending" if plan.sort_descending else "ascending"
        )

        message = (
            f"Sorted by {plan.sort_column} ({direction}). "
            f"{_format_row_preview(sorted_rows)}"
        )

        return message, sorted_rows

    if plan.operation == ResultAnalysisOperation.RANK:

        ranked_rows = _sort_rows(
            rows, plan.sort_column, plan.sort_descending
        )

        lines = [
            f"{index}. "
            + ", ".join(
                f"{key}={_json_safe_value(value)}"
                for key, value in row.items()
            )
            for index, row in enumerate(
                ranked_rows[:10], start=1
            )
        ]

        message = (
            f"Ranked by {plan.sort_column}:\n"
            + "\n".join(lines)
        )

        return message, ranked_rows

    if plan.operation in (
        ResultAnalysisOperation.TOP_N,
        ResultAnalysisOperation.BOTTOM_N,
    ):

        descending = (
            plan.operation == ResultAnalysisOperation.TOP_N
        )

        ordered = _sort_rows(
            rows, plan.sort_column, descending
        )

        selected = ordered[: plan.n]

        label = "Top" if descending else "Bottom"

        message = (
            f"{label} {len(selected)} row(s) by "
            f"{plan.sort_column}: "
            f"{_format_row_preview(selected, max_rows=len(selected))}"
        )

        return message, selected

    if plan.operation == ResultAnalysisOperation.FILTER:

        filtered = _filter_rows(
            rows, plan.filter_column, plan.filter_value
        )

        message = (
            f"Filtered where {plan.filter_column} = "
            f"{plan.filter_value}: {len(filtered)} "
            f"matching row(s). "
            f"{_format_row_preview(filtered)}"
        )

        return message, filtered

    if plan.operation == ResultAnalysisOperation.SELECT_COLUMNS:

        selected_rows = _select_columns(
            rows, plan.target_columns
        )

        message = (
            "Showing columns: "
            f"{', '.join(plan.target_columns)}. "
            f"{_format_row_preview(selected_rows, columns=plan.target_columns)}"
        )

        return message, selected_rows

    if plan.operation == ResultAnalysisOperation.LIMIT:

        limited = rows[: plan.n]

        message = (
            f"Showing the first {len(limited)} row(s). "
            f"{_format_row_preview(limited, max_rows=len(limited))}"
        )

        return message, limited

    raise AssertionError(
        f"Unreachable deterministic operation: {plan.operation}"
    )


_CLARIFICATION_MESSAGES = {
    "requested_column_not_found": (
        "I could not match that to a column in the "
        "previous result. Please name one of the "
        "available columns."
    ),
    "operation_target_missing": (
        "I need a bit more detail (such as a column "
        "name or a number) to do that with the previous "
        "result."
    ),
    "aggregate_column_not_numeric": (
        "That column does not contain numeric values, "
        "so I cannot compute that for it."
    ),
    "operation_unclear": (
        "I could not determine what you would like me "
        "to do with the previous result. Please be more "
        "specific."
    ),
}


def _clarification_message(reason_code: Optional[str]) -> str:

    return _CLARIFICATION_MESSAGES.get(
        reason_code or "operation_unclear",
        _CLARIFICATION_MESSAGES["operation_unclear"],
    )


class ResultAnalysisService:
    """
    Analyse previously returned result rows.

    This is different from generate_result_analysis():

    generate_result_analysis()
        -> immediate summary after SQL execution

    ResultAnalysisService
        -> later conversational follow-up against
           rows already stored in chat history

    This service does NOT:
    - execute SQL
    - query Oracle
    - use FAISS
    - use Secondary

    An LLM is used only to classify which operation is being
    requested (see _OPERATION_CLASSIFIER_SYSTEM_PROMPT) and, for the
    purely semantic operations (summarize/explain/compare), to phrase
    the final answer. Sorting, filtering, limiting, counting,
    ranking, top/bottom N, column selection, and numeric aggregates
    are always computed in plain Python.
    """

    def __init__(
        self,
        *,
        pool,
        model: Optional[str] = None,
        max_rows: int = 100,
    ) -> None:

        self.pool = pool

        self.model = (
            model
            or "gpt-5-mini"
        )

        self.max_rows = max(
            1,
            int(max_rows),
        )

    async def analyze(
        self,
        *,
        user_input: str,
        result_context: Dict[
            str,
            Any,
        ],
        report_name: Optional[
            str
        ] = None,
    ) -> ResultAnalysisOutput:
        """
        Answer a follow-up question using previously
        stored result rows.
        """

        cleaned_input = str(
            user_input or ""
        ).strip()

        if not cleaned_input:

            raise ResultAnalysisServiceError(
                "User input cannot be empty.",
                code="empty_user_input",
            )

        rows = (
            result_context.get(
                "table_rows"
            )
            or []
        )

        if not isinstance(
            rows,
            list,
        ):

            rows = []

        if not rows:

            return ResultAnalysisOutput(
                outcome=ResultAnalysisOutcome.ANALYZED,
                message=(
                    "The previous result had no rows "
                    "to analyze."
                ),
                rows_available=0,
                rows_used=0,
                truncated=False,
            )

        available_columns = _ordered_columns(rows)

        raw_plan = await self._classify_operation(
            user_input=cleaned_input,
            available_columns=available_columns,
        )

        plan = self._validate_and_normalize_plan(
            raw_plan, available_columns
        )

        if plan.requires_clarification:

            return ResultAnalysisOutput(
                outcome=(
                    ResultAnalysisOutcome
                    .CLARIFICATION_REQUIRED
                ),
                message=_clarification_message(
                    plan.clarification_reason
                ),
                rows_available=len(rows),
                rows_used=0,
                truncated=False,
                operation=plan.operation.value,
                reason_code=plan.clarification_reason,
            )

        if plan.scope == ResultAnalysisScope.FULL_DATASET:

            return ResultAnalysisOutput(
                outcome=(
                    ResultAnalysisOutcome
                    .DATABASE_DATA_REQUIRED
                ),
                message=(
                    "Answering that would require the "
                    "complete underlying data, not just "
                    "the previously returned rows. "
                    "Please ask me to retrieve that data "
                    "directly."
                ),
                rows_available=len(rows),
                rows_used=0,
                truncated=False,
                operation=plan.operation.value,
                reason_code="full_dataset_required",
            )

        if plan.operation == ResultAnalysisOperation.COUNT:

            total = len(rows)

            return ResultAnalysisOutput(
                outcome=ResultAnalysisOutcome.ANALYZED,
                message=(
                    f"There are {total} "
                    f"row{'s' if total != 1 else ''} "
                    "in the previous result."
                ),
                rows_available=total,
                rows_used=total,
                truncated=False,
                operation=plan.operation.value,
            )

        rows_available = len(rows)

        rows_for_analysis = (
            rows[:self.max_rows]
        )

        rows_used = len(
            rows_for_analysis
        )

        truncated = (
            rows_available
            > rows_used
        )

        if plan.operation in _DETERMINISTIC_OPERATIONS:

            message, result_rows = (
                _execute_deterministic_plan(
                    plan, rows_for_analysis
                )
            )

            return ResultAnalysisOutput(
                outcome=ResultAnalysisOutcome.ANALYZED,
                message=message,
                rows_available=rows_available,
                rows_used=rows_used,
                truncated=truncated,
                operation=plan.operation.value,
                result_rows=result_rows,
            )

        # ====================================================
        # SEMANTIC FALLBACK: summarize / explain / compare
        # ====================================================

        message = await self._call_semantic_analysis(
            user_input=cleaned_input,
            rows_for_analysis=rows_for_analysis,
            rows_available=rows_available,
            rows_used=rows_used,
            truncated=truncated,
            report_name=report_name,
            result_context=result_context,
        )

        return ResultAnalysisOutput(
            outcome=ResultAnalysisOutcome.ANALYZED,
            message=message,
            rows_available=rows_available,
            rows_used=rows_used,
            truncated=truncated,
            operation=plan.operation.value,
        )

    # ========================================================
    # OPERATION CLASSIFICATION (LLM understands, never computes)
    # ========================================================

    async def _classify_operation(
        self,
        *,
        user_input: str,
        available_columns: List[str],
    ) -> _RawResultAnalysisPlan:

        payload = {
            "follow_up_question": user_input,
            "available_columns": available_columns,
        }

        user_prompt = (
            "Classify the requested analysis operation.\n\n"
            + json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )

        response_text = await self._call_model(
            system_prompt=(
                _OPERATION_CLASSIFIER_SYSTEM_PROMPT
            ),
            user_prompt=user_prompt,
            empty_response_code=(
                "empty_operation_classifier_response"
            ),
            model_call_failed_code=(
                "operation_classifier_model_failed"
            ),
        )

        cleaned_response = self._clean_json_response(
            response_text
        )

        try:

            return (
                _RawResultAnalysisPlan
                .model_validate_json(
                    cleaned_response
                )
            )

        except ValidationError as error:

            raise ResultAnalysisServiceError(
                "The operation classifier returned an "
                "invalid structured response.",
                code="invalid_operation_classifier_response",
                details={
                    "validation_errors": error.errors(),
                    "raw_response": response_text,
                },
            ) from error

        except ValueError as error:

            raise ResultAnalysisServiceError(
                "The operation classifier returned "
                "invalid JSON.",
                code="invalid_operation_classifier_json",
                details={
                    "raw_response": response_text,
                },
            ) from error

    @staticmethod
    def _validate_and_normalize_plan(
        raw: _RawResultAnalysisPlan,
        available_columns: List[str],
    ) -> ResultAnalysisPlan:
        """
        Deterministic post-LLM validation. Never trusts a column
        name the LLM supplied unless it actually exists in the
        stored result (case-insensitively) - this is what rejects
        both a genuinely missing column and a hallucinated one.
        """

        if (
            raw.clarification_required
            or raw.operation
            == ResultAnalysisOperation.OTHER
        ):

            return ResultAnalysisPlan(
                operation=raw.operation,
                scope=raw.scope,
                requires_clarification=True,
                clarification_reason=(
                    raw.clarification_reason
                    or "operation_unclear"
                ),
            )

        normalized_lookup = {
            str(column).strip().casefold(): column
            for column in available_columns
        }

        def _resolve(
            name: Optional[str],
        ) -> Tuple[Optional[str], bool]:

            if not name:
                return None, True

            key = str(name).strip().casefold()

            if key in normalized_lookup:
                return normalized_lookup[key], True

            return name, False

        resolved_targets = []
        targets_ok = True

        for column in raw.target_columns:
            resolved, ok = _resolve(column)
            resolved_targets.append(resolved)
            targets_ok = targets_ok and ok

        resolved_filter_column, filter_ok = _resolve(
            raw.filter_column
        )
        resolved_sort_column, sort_ok = _resolve(
            raw.sort_column
        )
        resolved_aggregate_column, aggregate_ok = _resolve(
            raw.aggregate_column
        )

        if not (
            targets_ok
            and filter_ok
            and sort_ok
            and aggregate_ok
        ):

            return ResultAnalysisPlan(
                operation=raw.operation,
                scope=raw.scope,
                requires_clarification=True,
                clarification_reason=(
                    "requested_column_not_found"
                ),
            )

        # ----------------------------------------------------
        # PER-OPERATION REQUIRED-FIELD VALIDATION
        # ----------------------------------------------------

        missing_target = False

        if raw.operation in (
            ResultAnalysisOperation.SORT,
            ResultAnalysisOperation.RANK,
        ):
            missing_target = not resolved_sort_column

        elif raw.operation in (
            ResultAnalysisOperation.TOP_N,
            ResultAnalysisOperation.BOTTOM_N,
        ):
            missing_target = (
                not resolved_sort_column
                or not raw.n
                or raw.n <= 0
            )

        elif raw.operation == ResultAnalysisOperation.FILTER:
            missing_target = (
                not resolved_filter_column
                or raw.filter_value in (None, "")
            )

        elif (
            raw.operation
            == ResultAnalysisOperation.AGGREGATE
        ):
            missing_target = (
                not resolved_aggregate_column
                or raw.aggregate_function is None
            )

        elif (
            raw.operation
            == ResultAnalysisOperation.SELECT_COLUMNS
        ):
            missing_target = not resolved_targets

        elif raw.operation == ResultAnalysisOperation.LIMIT:
            missing_target = not raw.n or raw.n <= 0

        if missing_target:

            return ResultAnalysisPlan(
                operation=raw.operation,
                scope=raw.scope,
                requires_clarification=True,
                clarification_reason=(
                    "operation_target_missing"
                ),
            )

        return ResultAnalysisPlan(
            operation=raw.operation,
            scope=raw.scope,
            target_columns=resolved_targets,
            filter_column=resolved_filter_column,
            filter_value=raw.filter_value,
            sort_column=resolved_sort_column,
            sort_descending=raw.sort_descending,
            n=raw.n,
            aggregate_function=raw.aggregate_function,
            aggregate_column=resolved_aggregate_column,
            requires_clarification=False,
            clarification_reason=None,
        )

    # ========================================================
    # SEMANTIC ANALYSIS (summarize / explain / compare)
    # ========================================================

    async def _call_semantic_analysis(
        self,
        *,
        user_input: str,
        rows_for_analysis: List[Dict[str, Any]],
        rows_available: int,
        rows_used: int,
        truncated: bool,
        report_name: Optional[str],
        result_context: Dict[str, Any],
    ) -> str:

        # ========================================================
        # REUSE EXISTING DETERMINISTIC PROFILER
        # ========================================================

        profile = (
            _build_result_profile(
                rows_for_analysis
            )
        )

        effective_report_name = (
            str(
                report_name
                or result_context.get(
                    "report_name"
                )
                or ""
            ).strip()
            or None
        )

        original_request = (
            result_context.get(
                "source_user_input"
            )
        )

        previous_answer = (
            result_context.get(
                "assistant_message"
            )
        )

        # ========================================================
        # PROMPT
        # ========================================================

        system_prompt = """
You are the conversational data analyst for an enterprise
reporting assistant.

The user is asking a follow-up question about data that
was already retrieved earlier in the conversation.

You receive:
- the current follow-up question
- the original user request
- the previous assistant response
- the actual stored result rows
- a deterministic profile of those rows

Use ONLY this supplied information.

IMPORTANT RULES:

1. Answer the user's current follow-up question directly.

2. Analyse ALL supplied rows when needed.

3. Do not generate SQL.

4. Do not claim that you queried the database again.

5. Do not invent rows, values, units, causes, business
   rules, or facts that are not present in the supplied
   information.

6. You may calculate directly-supported observations such as:
   - totals
   - counts
   - differences
   - rankings
   - minimum
   - maximum
   - averages
   - simple percentages
   - comparisons

7. When the user asks which record has the highest or lowest
   value, use the ACTUAL ROWS to identify the corresponding
   record. Do not rely only on aggregate profile statistics.

8. When the user asks "why", distinguish carefully between:
   - what the available data demonstrates
   - what would require additional causal information

   Do not invent a cause.

9. When terms such as:
   - critical
   - risky
   - best
   - worst
   - important
   - high
   - low

   could refer to multiple possible metrics, infer the
   criterion only when the supplied columns and user's
   wording make it reasonably clear.

   Otherwise state what criterion is missing.

10. If several columns are relevant, compare them together
    instead of arbitrarily choosing one.

11. If the supplied rows are truncated, do not make claims
    about records that were not supplied.

12. Speak naturally as a business reporting assistant.

13. Do not use implementation terms such as:
    - SQL
    - MongoDB
    - database query
    - result profile
    - stored rows
    - workflow
    - agent

14. Keep the answer concise unless the user explicitly
    requests detailed analysis.
""".strip()

        payload = {
            "current_question": (
                user_input
            ),

            "report_name": (
                effective_report_name
            ),

            "original_request": (
                original_request
            ),

            "previous_answer": (
                previous_answer
            ),

            "rows_available": (
                rows_available
            ),

            "rows_used": (
                rows_used
            ),

            "truncated": (
                truncated
            ),

            "result_profile": (
                profile
            ),

            # IMPORTANT:
            # Raw rows are included because follow-up questions
            # such as "which buyer has the highest stock?" require
            # association between values and the corresponding row.
            "rows": [
                {
                    key: (
                        _json_safe_value(
                            value
                        )
                    )
                    for key, value
                    in row.items()
                }
                for row
                in rows_for_analysis
            ],
        }

        user_prompt = (
            "Answer the user's follow-up question "
            "using the supplied previous result.\n\n"
            + json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )

        # ========================================================
        # MODEL CALL
        # ========================================================

        return await self._call_model(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            empty_response_code=(
                "empty_result_analysis_response"
            ),
            model_call_failed_code=(
                "result_analysis_model_failed"
            ),
        )

    # ========================================================
    # SHARED MODEL CALL
    # ========================================================

    async def _call_model(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        empty_response_code: str,
        model_call_failed_code: str,
    ) -> str:

        try:

            client = (
                await self.pool
                .get_client()
            )

        except Exception as error:

            raise ResultAnalysisServiceError(
                (
                    "Could not acquire the "
                    "OpenAI client."
                ),
                code=(
                    "client_acquisition_failed"
                ),
                details={
                    "exception_type": (
                        type(
                            error
                        ).__name__
                    ),
                },
            ) from error

        try:

            async with (
                self.pool.semaphore
            ):

                response = (
                    await client.responses.create(
                        model=self.model,
                        instructions=(
                            system_prompt
                        ),
                        input=user_prompt,
                    )
                )

        except Exception as error:

            raise ResultAnalysisServiceError(
                (
                    "Result analysis model "
                    "request failed."
                ),
                code=model_call_failed_code,
                details={
                    "exception_type": (
                        type(
                            error
                        ).__name__
                    ),
                    "exception_message": (
                        str(error)
                    ),
                },
            ) from error

        output_text = str(
            getattr(
                response,
                "output_text",
                "",
            )
            or ""
        ).strip()

        if not output_text:

            raise ResultAnalysisServiceError(
                (
                    "The result analysis model "
                    "returned an empty response."
                ),
                code=empty_response_code,
            )

        return output_text

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _clean_json_response(
        response_text: str,
    ) -> str:

        cleaned = str(response_text or "").strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):].strip()

        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()

        if cleaned.endswith("```"):
            cleaned = cleaned[:-len("```")].strip()

        return cleaned
