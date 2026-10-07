from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from sqlglot import exp, parse_one

from agents.query_executor import run_sql
from core.errors import OracleQueryError
from core.models import (
    OracleSchemaValidationIssue,
    OracleSchemaValidationResult,
    SQLValidationResult,
)

if TYPE_CHECKING:
    from orchestration.request_db_execution_budget import (
        RequestDatabaseExecutionTracker,
    )


ORACLE_PSEUDOCOLUMNS = {
    "ROWNUM",
    "ROWID",
    "ORA_ROWSCN",
    "LEVEL",
    "CONNECT_BY_ISLEAF",
    "CONNECT_BY_ISCYCLE",
}


TABLE_COLUMNS_WITH_OWNER_SQL = """
SELECT
    OWNER,
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE
FROM ALL_TAB_COLUMNS
WHERE OWNER = :toc_owner
  AND TABLE_NAME = :toc_table
ORDER BY COLUMN_ID
"""


TABLE_COLUMNS_CURRENT_SCHEMA_SQL = """
SELECT
    OWNER,
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE
FROM ALL_TAB_COLUMNS
WHERE OWNER = SYS_CONTEXT(
    'USERENV',
    'CURRENT_SCHEMA'
)
AND TABLE_NAME = :toc_table
ORDER BY COLUMN_ID
"""


def _normalize_identifier(
    value: Any,
) -> str:
    return (
        str(value or "")
        .strip()
        .strip('"')
        .upper()
    )


def _get_row_value(
    row: Dict[str, Any],
    key: str,
):
    target_key = key.upper()

    for row_key, value in row.items():
        if (
            str(row_key).upper()
            == target_key
        ):
            return value

    return None


def _collect_derived_names(
    *,
    sql: str,
    dialect: str,
) -> tuple[
    Set[str],
    Set[str],
]:
    """
    Collect names that are SQL-derived rather than physical
    Oracle columns/tables.

    Example:

        SELECT 'Value' AS TYPE
        ...
        ORDER BY TYPE

    TYPE is not a physical Oracle column.
    """

    statement = parse_one(
        sql,
        read=dialect,
    )

    projection_aliases: Set[str] = set()
    derived_table_aliases: Set[str] = set()

    for alias in statement.find_all(
        exp.Alias
    ):
        alias_name = str(
            alias.alias or ""
        ).strip()

        if alias_name:
            projection_aliases.add(
                _normalize_identifier(
                    alias_name
                )
            )

    for cte in statement.find_all(
        exp.CTE
    ):
        cte_name = str(
            cte.alias_or_name or ""
        ).strip()

        if cte_name:
            derived_table_aliases.add(
                _normalize_identifier(
                    cte_name
                )
            )

    for subquery in statement.find_all(
        exp.Subquery
    ):
        alias_name = str(
            subquery.alias_or_name or ""
        ).strip()

        if alias_name:
            derived_table_aliases.add(
                _normalize_identifier(
                    alias_name
                )
            )

    return (
        projection_aliases,
        derived_table_aliases,
    )


async def validate_sql_against_oracle_schema(
    *,
    sql: str,
    sql_validation_result: SQLValidationResult,
    dialect: str = "oracle",
    query_runner: Optional[
        Callable[..., List[Dict[str, Any]]]
    ] = None,
    db_execution_tracker: Optional[
        "RequestDatabaseExecutionTracker"
    ] = None,
) -> OracleSchemaValidationResult:
    """
    Validate generated SQL against live Oracle metadata.

    Oracle is the first and authoritative physical source of
    truth.

    Important distinction:

        success=False
            Oracle metadata lookup itself failed.

        success=True, valid=False
            Oracle answered successfully and confirmed that a
            referenced table/column is invalid.

    Fallback must only happen when success=False.
    """

    runner = query_runner or run_sql

    if not sql_validation_result.valid:
        return OracleSchemaValidationResult(
            success=False,
            valid=False,
            message=(
                "Oracle schema validation was not "
                "run because deterministic SQL "
                "validation failed."
            ),
            error_code=(
                "sql_validation_required"
            ),
        )

    try:
        (
            projection_aliases,
            derived_table_aliases,
        ) = _collect_derived_names(
            sql=sql,
            dialect=dialect,
        )

    except Exception as error:
        return OracleSchemaValidationResult(
            success=False,
            valid=False,
            message=(
                "SQL could not be prepared for "
                "Oracle schema validation."
            ),
            error_code=(
                "oracle_schema_parse_failed"
            ),
            error_message=str(error),
            diagnostics={
                "exception_type": (
                    type(error).__name__
                ),
            },
        )

    issues: List[
        OracleSchemaValidationIssue
    ] = []

    checked_tables: List[str] = []

    available_columns: Dict[
        str,
        List[str],
    ] = {}

    table_column_sets: Dict[
        str,
        Set[str],
    ] = {}

    table_reference_by_key = {}

    alias_lookup: Dict[
        str,
        List[str],
    ] = defaultdict(list)

    metadata_cache = {}

    # ========================================================
    # Load Oracle metadata for each physical table
    # ========================================================

    for table in (
        sql_validation_result.tables
    ):
        schema_name = str(
            table.schema_name or ""
        ).strip() or None

        table_name = str(
            table.name or ""
        ).strip()

        qualified_name = str(
            table.qualified_name
            or table_name
        ).strip()

        owner = (
            _normalize_identifier(
                schema_name
            )
            if schema_name
            else ""
        )

        normalized_table_name = (
            _normalize_identifier(
                table_name
            )
        )

        cache_key = (
            owner,
            normalized_table_name,
        )

        try:
            if cache_key not in metadata_cache:

                if db_execution_tracker is not None:
                    reservation = (
                        db_execution_tracker.try_reserve(
                            reason_code=(
                                "oracle_schema_validation"
                            ),
                            source_stage=(
                                "oracle_schema_validator"
                            ),
                        )
                    )

                    if not reservation.granted:
                        return OracleSchemaValidationResult(
                            success=False,
                            valid=False,
                            source="oracle",
                            authoritative=True,
                            message=(
                                "Oracle schema validation could "
                                "not be completed because the "
                                "request-level database execution "
                                "safety limit was reached."
                            ),
                            error_code=(
                                "request_db_execution_limit_reached"
                            ),
                            diagnostics={
                                "schema_name": schema_name,
                                "table_name": table_name,
                            },
                        )

                if schema_name:
                    metadata_sql = (
                        TABLE_COLUMNS_WITH_OWNER_SQL
                    )

                    binds = {
                        "toc_owner": owner,
                        "toc_table": (
                            normalized_table_name
                        ),
                    }

                else:
                    metadata_sql = (
                        TABLE_COLUMNS_CURRENT_SCHEMA_SQL
                    )

                    binds = {
                        "toc_table": (
                            normalized_table_name
                        ),
                    }

                rows = await asyncio.to_thread(
                    runner,
                    metadata_sql,
                    2000,
                    binds,
                )

                metadata_cache[
                    cache_key
                ] = rows or []

            rows = metadata_cache[
                cache_key
            ]

        except OracleQueryError as error:
            return OracleSchemaValidationResult(
                success=False,
                valid=False,
                source="oracle",
                authoritative=True,
                message=(
                    "Oracle metadata lookup failed."
                ),
                error_code=(
                    error.code
                    or (
                        "oracle_metadata_"
                        "lookup_failed"
                    )
                ),
                error_message=(
                    error.message
                ),
                diagnostics={
                    "schema_name": (
                        schema_name
                    ),
                    "table_name": (
                        table_name
                    ),
                    "oracle_details": (
                        error.details
                    ),
                },
            )

        except Exception as error:
            return OracleSchemaValidationResult(
                success=False,
                valid=False,
                source="oracle",
                authoritative=True,
                message=(
                    "Oracle metadata lookup failed."
                ),
                error_code=(
                    "oracle_metadata_lookup_failed"
                ),
                error_message=str(error),
                diagnostics={
                    "schema_name": (
                        schema_name
                    ),
                    "table_name": (
                        table_name
                    ),
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )

        column_names: List[str] = []

        for row in rows:
            column_name = str(
                _get_row_value(
                    row,
                    "COLUMN_NAME",
                )
                or ""
            ).strip()

            if column_name:
                column_names.append(
                    column_name
                )

        checked_tables.append(
            qualified_name
        )

        available_columns[
            qualified_name
        ] = column_names

        table_key = (
            _normalize_identifier(
                qualified_name
            )
        )

        table_column_sets[
            table_key
        ] = {
            _normalize_identifier(
                column_name
            )
            for column_name
            in column_names
        }

        table_reference_by_key[
            table_key
        ] = table

        # No rows from ALL_TAB_COLUMNS means Oracle did not find
        # this table/view under that owner.
        if not column_names:
            issues.append(
                OracleSchemaValidationIssue(
                    code=(
                        "oracle_table_not_found"
                    ),
                    message=(
                        "Generated SQL references "
                        "a table that Oracle could "
                        "not find."
                    ),
                    schema_name=(
                        schema_name
                    ),
                    table_name=(
                        table_name
                    ),
                    details={
                        "qualified_name": (
                            qualified_name
                        ),
                    },
                )
            )

        # Build aliases used later by column validation.
        possible_names = [
            table.alias,
            table.name,
            table.qualified_name,
        ]

        for possible_name in (
            possible_names
        ):
            alias_key = (
                _normalize_identifier(
                    possible_name
                )
            )

            if not alias_key:
                continue

            if (
                table_key
                not in alias_lookup[
                    alias_key
                ]
            ):
                alias_lookup[
                    alias_key
                ].append(
                    table_key
                )

    # ========================================================
    # Validate physical columns
    # ========================================================

    for column in (
        sql_validation_result.columns
    ):
        column_name = str(
            column.name or ""
        ).strip()

        column_key = (
            _normalize_identifier(
                column_name
            )
        )

        table_alias_key = (
            _normalize_identifier(
                column.table_alias
            )
        )

        if column.is_star:
            continue

        if (
            column_key
            in ORACLE_PSEUDOCOLUMNS
        ):
            continue

        # Example:
        #
        # SELECT 'Value' AS TYPE ...
        # ORDER BY TYPE
        #
        # TYPE is not a physical column.
        if (
            not table_alias_key
            and column_key
            in projection_aliases
        ):
            continue

        # CTE/subquery alias.
        if (
            table_alias_key
            and table_alias_key
            in derived_table_aliases
        ):
            continue

        if table_alias_key:
            candidate_table_keys = (
                alias_lookup.get(
                    table_alias_key,
                    []
                )
            )

            if not candidate_table_keys:
                issues.append(
                    OracleSchemaValidationIssue(
                        code=(
                            "oracle_table_alias_"
                            "not_resolved"
                        ),
                        message=(
                            "Generated SQL references "
                            "an unresolved table alias."
                        ),
                        column_name=(
                            column_name
                        ),
                        details={
                            "table_alias": (
                                column.table_alias
                            ),
                        },
                    )
                )

                continue

        else:
            candidate_table_keys = list(
                table_column_sets.keys()
            )

        matched_tables: List[str] = []

        checked_column_tables: List[
            str
        ] = []

        for table_key in (
            candidate_table_keys
        ):
            table_reference = (
                table_reference_by_key.get(
                    table_key
                )
            )

            if table_reference is None:
                continue

            qualified_table = str(
                table_reference.qualified_name
                or table_reference.name
                or ""
            ).strip()

            checked_column_tables.append(
                qualified_table
            )

            if (
                column_key
                in table_column_sets.get(
                    table_key,
                    set(),
                )
            ):
                matched_tables.append(
                    qualified_table
                )

        if not matched_tables:
            issues.append(
                OracleSchemaValidationIssue(
                    code=(
                        "oracle_column_not_found"
                    ),
                    message=(
                        "Generated SQL references "
                        "a column that Oracle could "
                        "not find in the applicable "
                        "table."
                    ),
                    column_name=(
                        column_name
                    ),
                    details={
                        "table_alias": (
                            column.table_alias
                        ),
                        "checked_tables": (
                            checked_column_tables
                        ),
                    },
                )
            )

            continue

        # Unqualified column exists in multiple joined tables.
        # Oracle may treat this as ambiguous.
        if (
            not table_alias_key
            and len(matched_tables) > 1
        ):
            issues.append(
                OracleSchemaValidationIssue(
                    code=(
                        "oracle_column_ambiguous"
                    ),
                    message=(
                        "An unqualified SQL column "
                        "exists in multiple referenced "
                        "tables."
                    ),
                    column_name=(
                        column_name
                    ),
                    details={
                        "matching_tables": (
                            matched_tables
                        ),
                    },
                )
            )

    valid = not issues

    return OracleSchemaValidationResult(
        success=True,
        valid=valid,
        source="oracle",
        authoritative=True,
        checked_tables=(
            checked_tables
        ),
        available_columns=(
            available_columns
        ),
        issues=issues,
        message=(
            (
                "Generated SQL references valid "
                "Oracle tables and columns."
            )
            if valid
            else (
                "Generated SQL contains table or "
                "column references that are not "
                "valid in Oracle."
            )
        ),
        diagnostics={
            "table_count": len(
                checked_tables
            ),
            "issue_count": len(
                issues
            ),
        },
    )