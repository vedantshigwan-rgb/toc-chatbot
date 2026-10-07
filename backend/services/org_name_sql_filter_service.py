from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from services.extra_sql_filter_service import (
    SAFE_IDENTIFIER_PATTERN,
    _base_table_name,
    _direct_tables_for_select,
    _get_mapping_candidates,
    _normalize_key,
    _table_qualifier,
)


def _is_present_variable_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(
            _is_present_variable_value(item)
            for item in value
        )
    return True


def _org_name_variables(
    variables: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Return every usable org_name variable regardless of source."""

    matches: List[Dict[str, Any]] = []

    for item in variables or []:
        if not isinstance(item, dict):
            continue

        variable_name = str(
            item.get("name") or ""
        ).strip().lower()

        if variable_name != "org_name":
            continue

        value = item.get("value")
        if not _is_present_variable_value(value):
            continue

        matches.append(dict(item))

    return matches


def _build_contains_filter_condition(
    *,
    qualifier: str,
    column_name: str,
    variable_name: str,
    dialect: str,
):
    """Build TABLE_ALIAS.COLUMN LIKE '%{variable_name}%' safely."""

    if not SAFE_IDENTIFIER_PATTERN.fullmatch(
        column_name
    ):
        raise ValueError(
            "Unsafe mapped column name: "
            f"{column_name}"
        )

    if (
        qualifier
        and not SAFE_IDENTIFIER_PATTERN.fullmatch(
            qualifier
        )
    ):
        raise ValueError(
            "Unsafe SQL table alias/name: "
            f"{qualifier}"
        )

    if not SAFE_IDENTIFIER_PATTERN.fullmatch(
        variable_name
    ):
        raise ValueError(
            "Unsafe variable name: "
            f"{variable_name}"
        )

    left_side = (
        f"{qualifier}.{column_name}"
        if qualifier
        else column_name
    )

    condition_sql = (
        f"{left_side} LIKE '%{{{variable_name}}}%'"
    )

    return parse_one(
        condition_sql,
        read=dialect,
    )


def _matches_mapped_column(
    column: exp.Column,
    *,
    select: exp.Select,
    column_name: str,
    qualifier_names: set[str],
) -> bool:
    # Do not inspect a nested SELECT while rewriting the current SELECT.
    if column.find_ancestor(exp.Select) is not select:
        return False

    if (
        _normalize_key(column.name)
        != _normalize_key(column_name)
    ):
        return False

    column_table = _normalize_key(
        column.table
    )

    # Unqualified mapped columns are accepted because the enclosing SELECT
    # has already been proven to directly read the mapped physical table.
    if not column_table:
        return True

    return column_table in qualifier_names


def _expression_references_mapped_column(
    expression: exp.Expression,
    *,
    select: exp.Select,
    column_name: str,
    qualifier_names: set[str],
) -> bool:
    for column in expression.find_all(
        exp.Column
    ):
        if _matches_mapped_column(
            column,
            select=select,
            column_name=column_name,
            qualifier_names=qualifier_names,
        ):
            return True

    return False


def _strip_mapped_column_predicates(
    expression: Optional[exp.Expression],
    *,
    select: exp.Select,
    column_name: str,
    qualifier_names: set[str],
) -> tuple[Optional[exp.Expression], int]:
    """
    Remove existing WHERE predicates that constrain the mapped org column.

    The remaining WHERE tree is retained. A fresh authoritative org_name
    condition is added afterwards, so an LLM-generated ORG_NAME condition
    never competes with the deterministic filter.
    """

    if expression is None:
        return None, 0

    if isinstance(
        expression,
        exp.Paren,
    ):
        (
            cleaned_inner,
            removed,
        ) = _strip_mapped_column_predicates(
            expression.this,
            select=select,
            column_name=column_name,
            qualifier_names=qualifier_names,
        )

        if cleaned_inner is None:
            return None, removed

        return (
            exp.Paren(
                this=cleaned_inner
            ),
            removed,
        )

    if isinstance(
        expression,
        (exp.And, exp.Or),
    ):
        (
            cleaned_left,
            removed_left,
        ) = _strip_mapped_column_predicates(
            expression.this,
            select=select,
            column_name=column_name,
            qualifier_names=qualifier_names,
        )

        (
            cleaned_right,
            removed_right,
        ) = _strip_mapped_column_predicates(
            expression.expression,
            select=select,
            column_name=column_name,
            qualifier_names=qualifier_names,
        )

        removed = (
            removed_left
            + removed_right
        )

        if (
            cleaned_left is None
            and cleaned_right is None
        ):
            return None, removed

        if cleaned_left is None:
            return cleaned_right, removed

        if cleaned_right is None:
            return cleaned_left, removed

        if isinstance(
            expression,
            exp.And,
        ):
            return (
                exp.and_(
                    cleaned_left,
                    cleaned_right,
                ),
                removed,
            )

        return (
            exp.or_(
                cleaned_left,
                cleaned_right,
            ),
            removed,
        )

    if _expression_references_mapped_column(
        expression,
        select=select,
        column_name=column_name,
        qualifier_names=qualifier_names,
    ):
        return None, 1

    return expression.copy(), 0


def enforce_org_name_sql_filter(
    *,
    sql_template: str,
    report_name: str,
    variables: List[Dict[str, Any]],
    allowed_tables: Optional[
        List[str]
    ] = None,
    mapping_workbook_path: Optional[
        str
    ] = None,
    dialect: str = "oracle",
) -> Dict[str, Any]:
    """
    Enforce the request's org_name on an LLM-generated SQL template.

    This is intentionally independent of variable source. If org_name is
    present as source=input, source=option, or any future source label, the
    same deterministic rule is applied.

    Mapping source:
        redis mapping.xlsx
        report_name + org_name -> table_name + column_name

    Resulting predicate:
        MAPPED_COLUMN LIKE '%{org_name}%'

    If the generated WHERE already has a condition against the mapped org
    column, that predicate is removed and replaced by the authoritative one.
    If no such condition exists, the authoritative condition is ANDed into
    the existing WHERE or a new WHERE is created.

    Actual user values are not concatenated here. The existing
    sql_injection_service injects {org_name} later in the normal Primary
    pipeline.
    """

    cleaned_sql = str(
        sql_template or ""
    ).strip()

    org_variables = _org_name_variables(
        variables
    )

    if not cleaned_sql:
        return {
            "success": False,
            "message": "SQL template is empty.",
            "sql_template": cleaned_sql,
            "applied": False,
        }

    if not org_variables:
        return {
            "success": True,
            "message": (
                "No org_name variable was supplied."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "replaced_predicate_count": 0,
            "mapping_workbook_path": None,
        }

    try:
        (
            workbook_path,
            candidates,
        ) = _get_mapping_candidates(
            report_name=report_name,
            variable_name="org_name",
            mapping_workbook_path=(
                mapping_workbook_path
            ),
        )

    except Exception as error:
        return {
            "success": False,
            "message": (
                "Could not load the report-wise "
                "SQL mapping for org_name."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "exception_type": (
                type(error).__name__
            ),
            "details": {
                "internal_message": str(error),
            },
        }

    workbook_path_used = str(
        workbook_path
    )

    if not candidates:
        return {
            "success": False,
            "message": (
                "No org_name mapping exists for "
                f"report '{report_name}'."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "mapping_workbook_path": (
                workbook_path_used
            ),
        }

    allowed_base_tables = {
        _normalize_key(
            _base_table_name(
                table_name
            )
        )
        for table_name
        in allowed_tables or []
        if _base_table_name(
            table_name
        )
    }

    if allowed_base_tables:
        candidates = [
            candidate
            for candidate in candidates
            if (
                _normalize_key(
                    _base_table_name(
                        candidate.get(
                            "table_name"
                        )
                    )
                )
                in allowed_base_tables
            )
        ]

    if not candidates:
        return {
            "success": False,
            "message": (
                "The org_name mapping does not "
                "target a trusted table for "
                f"report '{report_name}'."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "mapping_workbook_path": (
                workbook_path_used
            ),
            "details": {
                "allowed_tables": (
                    allowed_tables or []
                ),
            },
        }

    had_semicolon = (
        cleaned_sql.endswith(";")
    )

    sql_without_semicolon = (
        cleaned_sql
        .rstrip(";")
        .strip()
    )

    try:
        expression = parse_one(
            sql_without_semicolon,
            read=dialect,
        )

    except ParseError as error:
        return {
            "success": False,
            "message": (
                "Generated SQL could not be "
                "parsed before enforcing org_name."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "exception_type": (
                type(error).__name__
            ),
            "details": {
                "parse_error": str(error),
            },
        }

    matching_candidates = []

    for candidate in candidates:
        mapped_base_table = (
            _normalize_key(
                _base_table_name(
                    candidate.get(
                        "table_name"
                    )
                )
            )
        )

        matching_selects = []

        for select in expression.find_all(
            exp.Select
        ):
            direct_tables = (
                _direct_tables_for_select(
                    select
                )
            )

            matching_tables = [
                table
                for table
                in direct_tables
                if (
                    _normalize_key(
                        _base_table_name(
                            table.name
                        )
                    )
                    == mapped_base_table
                )
            ]

            if matching_tables:
                matching_selects.append(
                    (
                        select,
                        matching_tables,
                    )
                )

        if matching_selects:
            matching_candidates.append(
                (
                    candidate,
                    matching_selects,
                )
            )

    if not matching_candidates:
        return {
            "success": False,
            "message": (
                "The generated SQL does not use "
                "the table mapped to org_name "
                f"for report '{report_name}'."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "mapping_workbook_path": (
                workbook_path_used
            ),
            "details": {
                "mapping_candidates": candidates,
            },
        }

    if len(matching_candidates) > 1:
        return {
            "success": False,
            "message": (
                "Multiple org_name mappings match "
                "tables used by the generated SQL."
            ),
            "sql_template": cleaned_sql,
            "applied": False,
            "mapping_workbook_path": (
                workbook_path_used
            ),
            "details": {
                "matching_mappings": [
                    candidate
                    for candidate, _
                    in matching_candidates
                ],
            },
        }

    (
        selected_mapping,
        matching_selects,
    ) = matching_candidates[0]

    column_name = str(
        selected_mapping.get(
            "column_name"
        )
        or ""
    ).strip()

    replaced_predicate_count = 0
    updated_select_count = 0

    for (
        select,
        matching_tables,
    ) in matching_selects:
        if len(matching_tables) != 1:
            return {
                "success": False,
                "message": (
                    "The org_name mapped table is "
                    "used more than once in the "
                    "same SELECT scope, so the "
                    "correct alias is ambiguous."
                ),
                "sql_template": cleaned_sql,
                "applied": False,
                "mapping_workbook_path": (
                    workbook_path_used
                ),
            }

        table = matching_tables[0]
        qualifier = _table_qualifier(
            table
        )

        qualifier_names = {
            _normalize_key(
                qualifier
            ),
            _normalize_key(
                table.name
            ),
            _normalize_key(
                _base_table_name(
                    table.name
                )
            ),
        }
        qualifier_names.discard("")

        try:
            enforced_condition = (
                _build_contains_filter_condition(
                    qualifier=qualifier,
                    column_name=column_name,
                    variable_name="org_name",
                    dialect=dialect,
                )
            )

        except Exception as error:
            return {
                "success": False,
                "message": (
                    "Could not build the "
                    "org_name SQL filter."
                ),
                "sql_template": cleaned_sql,
                "applied": False,
                "exception_type": (
                    type(error).__name__
                ),
                "details": {
                    "internal_message": (
                        str(error)
                    ),
                },
            }

        existing_where = select.args.get(
            "where"
        )

        if existing_where is None:
            base_condition = None
            removed_here = 0
        else:
            (
                base_condition,
                removed_here,
            ) = _strip_mapped_column_predicates(
                existing_where.this,
                select=select,
                column_name=column_name,
                qualifier_names=qualifier_names,
            )

        replaced_predicate_count += (
            removed_here
        )

        if base_condition is None:
            new_where_condition = (
                enforced_condition
            )
        else:
            new_where_condition = exp.and_(
                base_condition,
                enforced_condition,
            )

        select.set(
            "where",
            exp.Where(
                this=new_where_condition
            ),
        )

        updated_select_count += 1

    final_sql_template = (
        expression.sql(
            dialect=dialect
        ).strip()
    )

    if had_semicolon:
        final_sql_template += ";"

    return {
        "success": True,
        "message": (
            "org_name SQL filter was "
            "enforced successfully."
        ),
        "sql_template": final_sql_template,
        "applied": True,
        "variable_name": "org_name",
        "column_name": column_name,
        "table_name": str(
            selected_mapping.get(
                "table_name"
            )
            or ""
        ).strip(),
        "storage_type": str(
            selected_mapping.get(
                "storage_type"
            )
            or ""
        ).strip(),
        "select_scopes_updated": (
            updated_select_count
        ),
        "replaced_predicate_count": (
            replaced_predicate_count
        ),
        "variable_sources": sorted(
            {
                str(
                    item.get("source")
                    or "input"
                ).strip().lower()
                for item in org_variables
            }
        ),
        "mapping_workbook_path": (
            workbook_path_used
        ),
    }
