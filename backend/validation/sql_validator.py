from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from core.enums import (
    SQLValidationIssueCategory,
)
from core.models import (
    SQLColumnReference,
    SQLTableReference,
    SQLValidationIssue,
    SQLValidationResult,
)


PLACEHOLDER_PATTERN = re.compile(
    r"\{([A-Za-z_][A-Za-z0-9_]*)\}"
)


# These AST nodes represent statements or operations that the
# reporting chatbot must never execute.
FORBIDDEN_NODE_NAMES = {
    "insert",
    "update",
    "delete",
    "merge",
    "create",
    "drop",
    "alter",
    "truncate",
    "grant",
    "revoke",
    "commit",
    "rollback",
    "transaction",
    "command",
    "copy",
    "load",
    "replace",
    "into",
}


def _normalize_identifier(
    value: Optional[str],
) -> str:
    """
    Normalize an identifier for deterministic comparison.
    """

    normalized = str(value or "").strip()

    if (
        len(normalized) >= 2
        and normalized[0] == '"'
        and normalized[-1] == '"'
    ):
        normalized = normalized[1:-1]

    return normalized.upper()


def _qualified_table_name(
    table: exp.Table,
) -> str:
    """
    Return catalog.schema.table or schema.table when available.
    """

    parts = [
        str(table.catalog or "").strip(),
        str(table.db or "").strip(),
        str(table.name or "").strip(),
    ]

    return ".".join(
        part
        for part in parts
        if part
    )


def _collect_cte_names(
    statement: exp.Expression,
) -> Set[str]:
    """
    Collect CTE aliases so they are not incorrectly treated as
    physical database tables.
    """

    cte_names: Set[str] = set()

    for cte in statement.find_all(exp.CTE):
        alias_name = str(
            cte.alias_or_name or ""
        ).strip()

        if alias_name:
            cte_names.add(
                _normalize_identifier(alias_name)
            )

    return cte_names


def _collect_tables(
    statement: exp.Expression,
) -> List[SQLTableReference]:
    """
    Extract physical table references from the SQL AST.
    """

    cte_names = _collect_cte_names(
        statement
    )

    table_references: List[
        SQLTableReference
    ] = []

    seen_tables: Set[
        tuple[str, str]
    ] = set()

    for table in statement.find_all(exp.Table):
        table_name = str(
            table.name or ""
        ).strip()

        schema_name = str(
            table.db or ""
        ).strip() or None

        catalog_name = str(
            table.catalog or ""
        ).strip() or None

        if not table_name:
            continue

        normalized_table_name = (
            _normalize_identifier(table_name)
        )

        # A table reference with no schema/catalog that matches a
        # CTE alias is a logical CTE reference, not a physical
        # Oracle table.
        if (
            not schema_name
            and not catalog_name
            and normalized_table_name
            in cte_names
        ):
            continue

        qualified_name = (
            _qualified_table_name(table)
        )

        alias_name = str(
            table.alias or ""
        ).strip() or None

        deduplication_key = (
            _normalize_identifier(
                qualified_name
            ),
            _normalize_identifier(
                alias_name
            ),
        )

        if deduplication_key in seen_tables:
            continue

        seen_tables.add(
            deduplication_key
        )

        table_references.append(
            SQLTableReference(
                name=table_name,
                schema_name=schema_name,
                catalog_name=catalog_name,
                qualified_name=qualified_name,
                alias=alias_name,
            )
        )

    return table_references


def _collect_columns(
    statement: exp.Expression,
) -> List[SQLColumnReference]:
    """
    Extract column references from the SQL AST.
    """

    column_references: List[
        SQLColumnReference
    ] = []

    seen_columns: Set[
        tuple[str, str]
    ] = set()

    for column in statement.find_all(exp.Column):
        column_name = str(
            column.name or ""
        ).strip()

        table_alias = str(
            column.table or ""
        ).strip() or None

        if not column_name:
            continue

        qualified_name = (
            f"{table_alias}.{column_name}"
            if table_alias
            else column_name
        )

        deduplication_key = (
            _normalize_identifier(
                table_alias
            ),
            _normalize_identifier(
                column_name
            ),
        )

        if deduplication_key in seen_columns:
            continue

        seen_columns.add(
            deduplication_key
        )

        column_references.append(
            SQLColumnReference(
                name=column_name,
                table_alias=table_alias,
                qualified_name=qualified_name,
                is_star=column_name == "*",
            )
        )

    # SQLGlot can represent an unqualified SELECT * as Star rather
    # than Column, so capture those separately.
    has_unqualified_star = any(
        isinstance(node, exp.Star)
        for node in statement.walk()
    )

    if (
        has_unqualified_star
        and not any(
            reference.is_star
            for reference in column_references
        )
    ):
        column_references.append(
            SQLColumnReference(
                name="*",
                table_alias=None,
                qualified_name="*",
                is_star=True,
            )
        )

    return column_references


def _collect_functions(
    statement: exp.Expression,
) -> List[str]:
    """
    Extract unique SQL function names.
    """

    function_names: Set[str] = set()

    for function in statement.find_all(exp.Func):
        try:
            function_name = str(
                function.sql_name()
            ).strip()
        except Exception:
            function_name = (
                type(function).__name__
            )

        if function_name:
            function_names.add(
                function_name.upper()
            )

    return sorted(function_names)


def _collect_forbidden_operations(
    statement: exp.Expression,
) -> List[str]:
    """
    Return forbidden AST node names found in the statement.
    """

    operations: Set[str] = set()

    for node in statement.walk():
        node_name = (
            type(node).__name__.lower()
        )

        if node_name in FORBIDDEN_NODE_NAMES:
            operations.add(node_name)

    return sorted(operations)


def _is_table_allowed(
    *,
    table: SQLTableReference,
    allowed_tables: Iterable[str],
) -> bool:
    """
    Check a table against an allowlist.

    Allowlist values containing a dot require a qualified-name
    match. Values without a dot match only the base table name.
    """

    table_name = _normalize_identifier(
        table.name
    )

    qualified_name = _normalize_identifier(
        table.qualified_name
    )

    for allowed_table in allowed_tables:
        normalized_allowed = (
            _normalize_identifier(
                allowed_table
            )
        )

        if not normalized_allowed:
            continue

        if "." in normalized_allowed:
            if (
                normalized_allowed
                == qualified_name
            ):
                return True
        elif normalized_allowed == table_name:
            return True

    return False


def validate_sql(
    *,
    sql: str,
    dialect: str = "oracle",
    allowed_tables: Optional[List[str]] = None,
) -> SQLValidationResult:
    """
    Parse and validate generated SQL before Oracle execution.

    Validation performed:

    1. SQL must not be empty.
    2. No unresolved placeholders may remain.
    3. SQL must parse in the selected dialect.
    4. Only one statement is permitted.
    5. The statement must be a query.
    6. Mutating or administrative operations are rejected.
    7. Referenced tables can optionally be checked against an
       allowlist.
    8. Tables, columns and functions are extracted for later
       metadata validation.
    """

    executable_sql = str(
        sql or ""
    ).strip()

    issues: List[
        SQLValidationIssue
    ] = []

    unresolved_placeholders = sorted(
        set(
            PLACEHOLDER_PATTERN.findall(
                executable_sql
            )
        )
    )

    if not executable_sql:
        issues.append(
            SQLValidationIssue(
                category=(
                    SQLValidationIssueCategory
                    .EMPTY_SQL
                ),
                code="empty_sql",
                message=(
                    "Executable SQL is empty."
                ),
            )
        )

        return SQLValidationResult(
            valid=False,
            dialect=dialect,
            statement_count=0,
            unresolved_placeholders=[],
            issues=issues,
        )

    if unresolved_placeholders:
        issues.append(
            SQLValidationIssue(
                category=(
                    SQLValidationIssueCategory
                    .UNRESOLVED_PLACEHOLDER
                ),
                code=(
                    "unresolved_placeholders"
                ),
                message=(
                    "SQL contains unresolved "
                    "placeholders."
                ),
                details={
                    "placeholders": (
                        unresolved_placeholders
                    ),
                },
            )
        )

    try:
        statements = [
            statement
            for statement in parse(
                executable_sql,
                read=dialect,
            )
            if statement is not None
        ]

    except ParseError as error:
        issues.append(
            SQLValidationIssue(
                category=(
                    SQLValidationIssueCategory
                    .PARSE_ERROR
                ),
                code="sql_parse_error",
                message=str(error),
                details={
                    "dialect": dialect,
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )
        )

        return SQLValidationResult(
            valid=False,
            dialect=dialect,
            statement_count=0,
            unresolved_placeholders=(
                unresolved_placeholders
            ),
            issues=issues,
        )

    except Exception as error:
        issues.append(
            SQLValidationIssue(
                category=(
                    SQLValidationIssueCategory
                    .PARSE_ERROR
                ),
                code="sql_parser_failure",
                message=str(error),
                details={
                    "dialect": dialect,
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            )
        )

        return SQLValidationResult(
            valid=False,
            dialect=dialect,
            statement_count=0,
            unresolved_placeholders=(
                unresolved_placeholders
            ),
            issues=issues,
        )

    statement_count = len(statements)

    if statement_count != 1:
        issues.append(
            SQLValidationIssue(
                category=(
                    SQLValidationIssueCategory
                    .MULTIPLE_STATEMENTS
                ),
                code="invalid_statement_count",
                message=(
                    "Exactly one SQL statement is "
                    "permitted."
                ),
                details={
                    "statement_count": (
                        statement_count
                    ),
                },
            )
        )

    statement_types: List[str] = []
    table_references: List[
        SQLTableReference
    ] = []
    column_references: List[
        SQLColumnReference
    ] = []
    function_names: Set[str] = set()

    normalized_sql: Optional[str] = None

    for statement_number, statement in enumerate(
        statements,
        start=1,
    ):
        statement_type = (
            type(statement).__name__
        )

        statement_types.append(
            statement_type
        )

        if not isinstance(statement, exp.Query):
            issues.append(
                SQLValidationIssue(
                    category=(
                        SQLValidationIssueCategory
                        .NON_QUERY_STATEMENT
                    ),
                    code="non_query_statement",
                    message=(
                        "Only SELECT or WITH query "
                        "statements are permitted."
                    ),
                    details={
                        "statement_number": (
                            statement_number
                        ),
                        "statement_type": (
                            statement_type
                        ),
                    },
                )
            )

        forbidden_operations = (
            _collect_forbidden_operations(
                statement
            )
        )

        if forbidden_operations:
            issues.append(
                SQLValidationIssue(
                    category=(
                        SQLValidationIssueCategory
                        .UNSAFE_OPERATION
                    ),
                    code="unsafe_sql_operation",
                    message=(
                        "SQL contains a forbidden "
                        "operation."
                    ),
                    details={
                        "statement_number": (
                            statement_number
                        ),
                        "operations": (
                            forbidden_operations
                        ),
                    },
                )
            )

        table_references.extend(
            _collect_tables(statement)
        )

        column_references.extend(
            _collect_columns(statement)
        )

        function_names.update(
            _collect_functions(statement)
        )

    if (
        statement_count == 1
        and statements
    ):
        try:
            normalized_sql = statements[0].sql(
                dialect=dialect
            )
        except Exception:
            normalized_sql = None

    if allowed_tables is not None:
        for table_reference in table_references:
            if not _is_table_allowed(
                table=table_reference,
                allowed_tables=allowed_tables,
            ):
                issues.append(
                    SQLValidationIssue(
                        category=(
                            SQLValidationIssueCategory
                            .DISALLOWED_TABLE
                        ),
                        code="disallowed_table",
                        message=(
                            "SQL references a table "
                            "that is not allowed for "
                            "this workflow."
                        ),
                        details={
                            "table": (
                                table_reference
                                .qualified_name
                            ),
                            "allowed_tables": (
                                allowed_tables
                            ),
                        },
                    )
                )

    return SQLValidationResult(
        valid=not issues,
        dialect=dialect,
        statement_count=statement_count,
        statement_types=statement_types,
        tables=table_references,
        columns=column_references,
        functions=sorted(
            function_names
        ),
        unresolved_placeholders=(
            unresolved_placeholders
        ),
        issues=issues,
        normalized_sql=normalized_sql,
    )