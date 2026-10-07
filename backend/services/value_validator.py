from __future__ import annotations

import asyncio
import re
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)

from agents.query_executor import run_sql
from core.errors import OracleQueryError
from core.models import (
    SQLVariableBinding,
    ValueValidationItem,
    ValueValidationResult,
    VariableBindingResolutionResult,
)

if TYPE_CHECKING:
    from orchestration.request_db_execution_budget import (
        RequestDatabaseExecutionTracker,
    )


ORACLE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_$#]*$"
)


SUPPORTED_MATCH_TYPES = {
    "exact",
    "contains",
    "starts_with",
    "ends_with",
    "in",
}


def _normalize_variable_name(
    value: Any,
) -> str:
    return str(
        value or ""
    ).strip().casefold()


def _safe_identifier(
    value: Any,
    *,
    identifier_type: str,
) -> str:
    """
    Validate a SQL identifier before placing it into generated
    diagnostic SQL.

    Bind variables cannot be used for table and column names, so
    identifiers must come from the resolved SQL structure and must
    pass this strict validation.
    """

    identifier = str(
        value or ""
    ).strip()

    if not identifier:
        raise ValueError(
            f"{identifier_type} is empty."
        )

    if not ORACLE_IDENTIFIER_PATTERN.fullmatch(
        identifier
    ):
        raise ValueError(
            f"Unsafe Oracle {identifier_type}: "
            f"{identifier!r}"
        )

    return identifier.upper()


def _safe_table_name(
    binding: SQLVariableBinding,
) -> str:
    table_name = _safe_identifier(
        binding.table_name,
        identifier_type="table name",
    )

    if not binding.schema_name:
        return table_name

    schema_name = _safe_identifier(
        binding.schema_name,
        identifier_type="schema name",
    )

    return (
        f"{schema_name}."
        f"{table_name}"
    )


def _normalize_values(
    raw_value: Any,
) -> List[Any]:
    """
    Convert one scalar or multi-value input into a unique list.

    Outer whitespace is removed from strings. Internal whitespace
    is preserved.
    """

    if isinstance(
        raw_value,
        (list, tuple),
    ):
        values = list(raw_value)

    elif isinstance(raw_value, set):
        values = sorted(
            raw_value,
            key=lambda item: str(item),
        )

    else:
        values = [raw_value]

    normalized_values: List[Any] = []
    seen = set()

    for value in values:
        if value is None:
            continue

        normalized_value = value

        if isinstance(value, str):
            normalized_value = value.strip()

            if not normalized_value:
                continue

        deduplication_key = (
            type(normalized_value).__name__,
            repr(normalized_value),
        )

        if deduplication_key in seen:
            continue

        seen.add(deduplication_key)

        normalized_values.append(
            normalized_value
        )

    return normalized_values


def _prepare_bind_value(
    *,
    value: Any,
    match_type: str,
) -> Any:
    if match_type in {
        "exact",
        "in",
    }:
        return value

    text_value = str(value)

    if match_type == "contains":
        return f"%{text_value}%"

    if match_type == "starts_with":
        return f"{text_value}%"

    if match_type == "ends_with":
        return f"%{text_value}"

    raise ValueError(
        "Unsupported match type: "
        f"{match_type}"
    )


def _operator_for_match_type(
    match_type: str,
) -> str:
    if match_type in {
        "contains",
        "starts_with",
        "ends_with",
    }:
        return "LIKE"

    if match_type in {
        "exact",
        "in",
    }:
        return "="

    raise ValueError(
        "Unsupported match type: "
        f"{match_type}"
    )


def _build_validation_sql(
    *,
    qualified_table: str,
    column_name: str,
    operator: str,
) -> str:
    """
    Build a read-only individual-value existence query.

    ROWNUM is used outside the DISTINCT subquery so the number of
    values returned to Python remains bounded.
    """

    return (
        "SELECT MATCHED_VALUE "
        "FROM ("
        f"SELECT DISTINCT {column_name} "
        "AS MATCHED_VALUE "
        f"FROM {qualified_table} "
        f"WHERE {column_name} "
        f"{operator} :toc_value"
        ") "
        "WHERE ROWNUM <= :toc_limit"
    )


def _build_suggestion_sql(
    *,
    qualified_table: str,
    column_name: str,
) -> str:
    """
    Build a relaxed, case-insensitive suggestion query.

    This is run only when the normal value check returns no rows.
    """

    return (
        "SELECT MATCHED_VALUE "
        "FROM ("
        f"SELECT DISTINCT {column_name} "
        "AS MATCHED_VALUE "
        f"FROM {qualified_table} "
        f"WHERE UPPER({column_name}) "
        "LIKE UPPER(:toc_value)"
        ") "
        "WHERE ROWNUM <= :toc_limit"
    )


def _extract_matched_values(
    rows: Optional[Sequence[Any]],
) -> List[Any]:
    matched_values: List[Any] = []
    seen = set()

    for row in rows or []:
        value = None

        if isinstance(row, dict):
            for key, candidate in row.items():
                if (
                    str(key).strip().upper()
                    == "MATCHED_VALUE"
                ):
                    value = candidate
                    break

            if value is None and row:
                value = next(
                    iter(row.values())
                )

        elif isinstance(
            row,
            (list, tuple),
        ):
            if row:
                value = row[0]

        else:
            value = row

        if value is None:
            continue

        deduplication_key = (
            type(value).__name__,
            repr(value),
        )

        if deduplication_key in seen:
            continue

        seen.add(deduplication_key)
        matched_values.append(value)

    return matched_values


def _select_unique_binding(
    bindings: List[
        SQLVariableBinding
    ],
) -> Tuple[
    Optional[SQLVariableBinding],
    Optional[str],
]:
    """
    Multiple repeated predicates for the same physical target are
    acceptable.

    Multiple physical targets for one variable are ambiguous.
    """

    if not bindings:
        return (
            None,
            "binding_not_found",
        )

    targets = {
        (
            str(binding.schema_name or "")
            .strip()
            .casefold(),
            str(binding.table_name or "")
            .strip()
            .casefold(),
            str(binding.column_name or "")
            .strip()
            .casefold(),
        )
        for binding in bindings
    }

    if len(targets) > 1:
        return (
            None,
            "multiple_binding_targets",
        )

    return bindings[0], None


def _build_result_message(
    diagnosis: str,
) -> str:
    if diagnosis == "all_values_exist":
        return (
            "All supplied values exist individually, "
            "but no rows matched their combined filters "
            "or the query's additional conditions."
        )

    if diagnosis == "some_values_missing":
        return (
            "Some supplied values were not found in "
            "their resolved Oracle columns."
        )

    if diagnosis == "all_values_missing":
        return (
            "None of the supplied values were found "
            "in their resolved Oracle columns."
        )

    if diagnosis == "no_variables":
        return (
            "The query returned no rows and did not "
            "contain user-supplied variables to validate."
        )

    if diagnosis == "bindings_incomplete":
        return (
            "The query returned no rows, but value "
            "validation was skipped because variable "
            "bindings were incomplete."
        )

    return (
        "The query returned no rows, and value "
        "validation could not be completed."
    )


async def validate_bound_values(
    *,
    binding_result: (
        VariableBindingResolutionResult
    ),
    variable_values: Dict[str, Any],
    query_runner: Optional[
        Callable[..., List[Dict[str, Any]]]
    ] = None,
    max_matches: int = 10,
    max_suggestions: int = 5,
    db_execution_tracker: Optional[
        "RequestDatabaseExecutionTracker"
    ] = None,
) -> ValueValidationResult:
    """
    Validate supplied values independently against their resolved
    Oracle table columns.

    This function is intended to run only after the main SQL query
    executed successfully and returned zero rows.
    """

    runner = query_runner or run_sql

    normalized_variable_values = {
        _normalize_variable_name(
            variable_name
        ): _normalize_values(raw_value)
        for (
            variable_name,
            raw_value,
        ) in (
            variable_values or {}
        ).items()
    }

    normalized_variable_values = {
        variable_name: values
        for (
            variable_name,
            values,
        ) in normalized_variable_values.items()
        if variable_name and values
    }

    total_value_count = sum(
        len(values)
        for values in (
            normalized_variable_values.values()
        )
    )

    if total_value_count == 0:
        return ValueValidationResult(
            success=True,
            status="skipped",
            diagnosis="no_variables",
            message=_build_result_message(
                "no_variables"
            ),
            total_value_count=0,
            checked_count=0,
        )

    if (
        not binding_result.success
        or not binding_result.complete
    ):
        return ValueValidationResult(
            success=True,
            status="skipped",
            diagnosis="bindings_incomplete",
            message=_build_result_message(
                "bindings_incomplete"
            ),
            total_value_count=(
                total_value_count
            ),
            checked_count=0,
            diagnostics={
                "binding_success": (
                    binding_result.success
                ),
                "binding_complete": (
                    binding_result.complete
                ),
                "unresolved_variables": (
                    binding_result
                    .unresolved_variables
                ),
                "ambiguous_variables": (
                    binding_result
                    .ambiguous_variables
                ),
            },
        )

    bindings_by_variable: Dict[
        str,
        List[SQLVariableBinding],
    ] = {}

    for binding in binding_result.bindings:
        normalized_name = (
            _normalize_variable_name(
                binding.variable_name
            )
        )

        bindings_by_variable.setdefault(
            normalized_name,
            [],
        ).append(binding)

    items: List[
        ValueValidationItem
    ] = []

    for (
        variable_name,
        values,
    ) in normalized_variable_values.items():
        binding, binding_error = (
            _select_unique_binding(
                bindings_by_variable.get(
                    variable_name,
                    [],
                )
            )
        )

        if binding_error is not None:
            for value in values:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="skipped",
                        found=False,
                        error_code=(
                            binding_error
                        ),
                        error_message=(
                            "A unique physical "
                            "column binding could "
                            "not be selected."
                        ),
                    )
                )

            continue

        match_type = str(
            binding.match_type or ""
        ).strip().casefold()

        if (
            match_type
            not in SUPPORTED_MATCH_TYPES
        ):
            for value in values:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="skipped",
                        found=False,
                        schema_name=(
                            binding.schema_name
                        ),
                        table_name=(
                            binding.table_name
                        ),
                        column_name=(
                            binding.column_name
                        ),
                        operator=(
                            binding.operator
                        ),
                        match_type=match_type,
                        error_code=(
                            "unsupported_match_type"
                        ),
                        error_message=(
                            "The binding match type "
                            "is not supported by the "
                            "value validator."
                        ),
                    )
                )

            continue

        try:
            qualified_table = (
                _safe_table_name(binding)
            )

            column_name = (
                _safe_identifier(
                    binding.column_name,
                    identifier_type=(
                        "column name"
                    ),
                )
            )

            operator = (
                _operator_for_match_type(
                    match_type
                )
            )

            validation_sql = (
                _build_validation_sql(
                    qualified_table=(
                        qualified_table
                    ),
                    column_name=(
                        column_name
                    ),
                    operator=operator,
                )
            )

        except ValueError as error:
            for value in values:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="error",
                        found=False,
                        schema_name=(
                            binding.schema_name
                        ),
                        table_name=(
                            binding.table_name
                        ),
                        column_name=(
                            binding.column_name
                        ),
                        operator=(
                            binding.operator
                        ),
                        match_type=match_type,
                        error_code=(
                            "unsafe_binding_identifier"
                        ),
                        error_message=str(error),
                    )
                )

            continue

        for value in values:
            bind_value = (
                _prepare_bind_value(
                    value=value,
                    match_type=match_type,
                )
            )

            if db_execution_tracker is not None:
                reservation = (
                    db_execution_tracker.try_reserve(
                        reason_code="value_validation",
                        source_stage="value_validator",
                    )
                )

                if not reservation.granted:
                    items.append(
                        ValueValidationItem(
                            variable_name=variable_name,
                            submitted_value=value,
                            status="error",
                            found=False,
                            schema_name=binding.schema_name,
                            table_name=binding.table_name,
                            column_name=binding.column_name,
                            operator=operator,
                            match_type=match_type,
                            validation_sql=validation_sql,
                            error_code=(
                                "request_db_execution_limit_reached"
                            ),
                            error_message=(
                                "Value validation could not be "
                                "completed because the "
                                "request-level database execution "
                                "safety limit was reached."
                            ),
                        )
                    )

                    continue

            try:
                rows = await asyncio.to_thread(
                    runner,
                    validation_sql,
                    max_matches,
                    {
                        "toc_value": (
                            bind_value
                        ),
                        "toc_limit": (
                            max_matches
                        ),
                    },
                )

                matched_values = (
                    _extract_matched_values(
                        rows
                    )
                )

            except OracleQueryError as error:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="error",
                        found=False,
                        schema_name=(
                            binding.schema_name
                        ),
                        table_name=(
                            binding.table_name
                        ),
                        column_name=(
                            binding.column_name
                        ),
                        operator=operator,
                        match_type=match_type,
                        validation_sql=(
                            validation_sql
                        ),
                        error_code=error.code,
                        error_message=(
                            error.message
                        ),
                        diagnostics={
                            "oracle_details": (
                                error.details
                            ),
                        },
                    )
                )

                continue

            except Exception as error:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="error",
                        found=False,
                        schema_name=(
                            binding.schema_name
                        ),
                        table_name=(
                            binding.table_name
                        ),
                        column_name=(
                            binding.column_name
                        ),
                        operator=operator,
                        match_type=match_type,
                        validation_sql=(
                            validation_sql
                        ),
                        error_code=(
                            "value_validation_query_failed"
                        ),
                        error_message=str(error),
                        diagnostics={
                            "exception_type": (
                                type(error).__name__
                            ),
                        },
                    )
                )

                continue

            if matched_values:
                items.append(
                    ValueValidationItem(
                        variable_name=(
                            variable_name
                        ),
                        submitted_value=value,
                        status="found",
                        found=True,
                        schema_name=(
                            binding.schema_name
                        ),
                        table_name=(
                            binding.table_name
                        ),
                        column_name=(
                            binding.column_name
                        ),
                        operator=operator,
                        match_type=match_type,
                        matched_values=(
                            matched_values
                        ),
                        validation_sql=(
                            validation_sql
                        ),
                    )
                )

                continue

            suggested_values: List[Any] = []

            # Suggestions are optional. A suggestion-query failure
            # must not change a valid "missing" diagnosis into an
            # infrastructure failure. Likewise, if the request-level
            # DB execution safety limit has been reached, silently
            # skip the suggestion query rather than surfacing it as
            # an error - the primary "missing" diagnosis above is
            # unaffected.
            suggestion_blocked = False

            if isinstance(value, str) and db_execution_tracker is not None:
                suggestion_reservation = (
                    db_execution_tracker.try_reserve(
                        reason_code="value_validation_suggestion",
                        source_stage="value_validator",
                    )
                )

                suggestion_blocked = (
                    not suggestion_reservation.granted
                )

            if isinstance(value, str) and not suggestion_blocked:
                suggestion_sql = (
                    _build_suggestion_sql(
                        qualified_table=(
                            qualified_table
                        ),
                        column_name=(
                            column_name
                        ),
                    )
                )

                try:
                    suggestion_rows = (
                        await asyncio.to_thread(
                            runner,
                            suggestion_sql,
                            max_suggestions,
                            {
                                "toc_value": (
                                    f"%{value}%"
                                ),
                                "toc_limit": (
                                    max_suggestions
                                ),
                            },
                        )
                    )

                    suggested_values = (
                        _extract_matched_values(
                            suggestion_rows
                        )
                    )

                except Exception:
                    suggested_values = []

            items.append(
                ValueValidationItem(
                    variable_name=variable_name,
                    submitted_value=value,
                    status="missing",
                    found=False,
                    schema_name=(
                        binding.schema_name
                    ),
                    table_name=(
                        binding.table_name
                    ),
                    column_name=(
                        binding.column_name
                    ),
                    operator=operator,
                    match_type=match_type,
                    suggested_values=(
                        suggested_values
                    ),
                    validation_sql=(
                        validation_sql
                    ),
                )
            )

    found_count = sum(
        1
        for item in items
        if item.status == "found"
    )

    missing_count = sum(
        1
        for item in items
        if item.status == "missing"
    )

    error_count = sum(
        1
        for item in items
        if item.status == "error"
    )

    skipped_count = sum(
        1
        for item in items
        if item.status == "skipped"
    )

    checked_count = (
        found_count
        + missing_count
    )

    if error_count > 0:
        status = "failed"
        diagnosis = "validation_failed"
        success = False

    elif checked_count == 0:
        status = "skipped"
        diagnosis = "bindings_incomplete"
        success = True

    elif found_count == checked_count:
        status = "valid"
        diagnosis = "all_values_exist"
        success = True

    elif missing_count == checked_count:
        status = "invalid"
        diagnosis = "all_values_missing"
        success = True

    else:
        status = "partial"
        diagnosis = "some_values_missing"
        success = True

    return ValueValidationResult(
        success=success,
        status=status,
        diagnosis=diagnosis,
        message=_build_result_message(
            diagnosis
        ),
        total_value_count=(
            total_value_count
        ),
        checked_count=checked_count,
        found_count=found_count,
        missing_count=missing_count,
        error_count=error_count,
        skipped_count=skipped_count,
        items=items,
        diagnostics={
            "binding_count": len(
                binding_result.bindings
            ),
            "max_matches": max_matches,
            "max_suggestions": (
                max_suggestions
            ),
        },
    )