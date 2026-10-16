"""Oracle-only contextual autocomplete lookup. The Redis API owns all caching.

Physical identifiers come solely from the approved mapping workbook. User
values are bound, never interpolated. No arbitrary SQL or joins are accepted.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from agents.query_executor import get_connection

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_WORKBOOK = Path(__file__).resolve().parents[1] / "redis_mapping.xlsx"
_SCHEMA = "KPCL_REPORTING"
_MAX_FILTERS = 12
_MAX_VALUES_PER_FILTER = 10


class ContextualAutocompleteError(ValueError):
    pass


def normalize_identifier(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ContextualAutocompleteError("The approved report mapping contains an invalid SQL identifier.")
    return value.upper()


@lru_cache(maxsize=8)
def _load_mapping(file_mtime_ns: int) -> dict[str, dict[str, list[dict[str, str]]]]:
    workbook = load_workbook(_WORKBOOK, read_only=True, data_only=True)
    mapping: dict[str, dict[str, list[dict[str, str]]]] = {}
    try:
        sheet = workbook["Sheet1"]
        # Mapping columns are maintained in this exact order in the supplied workbook.
        for row in sheet.iter_rows(min_row=2, values_only=True):
            report, variable, column, table, storage = (list(row) + [None] * 5)[:5]
            if not all((report, variable, column, table)):
                continue
            report_id = normalize_identifier(report)
            variable_id = normalize_identifier(variable)
            record = {
                "column": _safe_identifier(str(column).strip()),
                "table": _safe_identifier(str(table).strip()),
                "storage": normalize_identifier(storage),
            }
            mapping.setdefault(report_id, {}).setdefault(variable_id, []).append(record)
    finally:
        workbook.close()
    return mapping


def _lookup(report: str, variable: str) -> dict[str, str]:
    if not _WORKBOOK.exists():
        raise ContextualAutocompleteError("Report variable mapping is unavailable.")
    catalog = _load_mapping(_WORKBOOK.stat().st_mtime_ns)
    report_variables = catalog.get(normalize_identifier(report))
    if not report_variables:
        raise ContextualAutocompleteError("Report is not configured for contextual autocomplete.")
    mappings = report_variables.get(normalize_identifier(variable), [])
    unique = {(m["column"], m["table"], m["storage"]) for m in mappings}
    if not unique:
        raise ContextualAutocompleteError(f"Variable '{variable}' is not mapped for this report.")
    if len(unique) != 1:
        raise ContextualAutocompleteError(f"Variable '{variable}' has multiple physical mappings; automatic lookup is not safe.")
    return mappings[0]


def normalize_filters(filters: dict[str, list[str]], target: str) -> dict[str, list[str]]:
    if len(filters) > _MAX_FILTERS:
        raise ContextualAutocompleteError("Too many autocomplete filters.")
    normalized: dict[str, list[str]] = {}
    target_id = normalize_identifier(target)
    for raw_name, raw_values in filters.items():
        name = normalize_identifier(raw_name)
        if not name or name == target_id:
            continue  # Never filter a facet against its own current value.
        if not isinstance(raw_values, list) or len(raw_values) > _MAX_VALUES_PER_FILTER:
            raise ContextualAutocompleteError("Invalid autocomplete filter values.")
        clean_values = sorted({str(v).strip() for v in raw_values if str(v).strip()}, key=str.casefold)
        if any(len(v) > 256 for v in clean_values):
            raise ContextualAutocompleteError("Autocomplete filter value is too long.")
        if clean_values:
            if name in normalized:
                normalized[name] = sorted(set(normalized[name] + clean_values), key=str.casefold)
            else:
                normalized[name] = clean_values
    return dict(sorted(normalized.items()))


def _virtual_docket_filters(report: str, filters: dict[str, list[str]], target: str) -> dict[str, list[str]]:
    """Translate Docket Report sidebar virtual period name into a real column.

    PERIOD_TYPE alone only chooses which period column PERIOD_NAME refers to.
    It is not a predicate against the physical Docket Report table.
    """
    if normalize_identifier(report) != "docket_report":
        return filters
    output = dict(filters)
    names = output.pop("period_name", [])
    kinds = output.pop("period_type", [])
    if names:
        if len(kinds) != 1:
            raise ContextualAutocompleteError("Select one PERIOD TYPE before PERIOD NAME.")
        period_mapping = {"year": "year_period", "month": "month_period", "week": "week_period"}
        actual = period_mapping.get(kinds[0].strip().lower())
        if not actual:
            raise ContextualAutocompleteError("Invalid PERIOD TYPE.")
        output[actual] = sorted(set(output.get(actual, []) + names), key=str.casefold)
    return output


def build_query(report: str, target: str, filters: dict[str, list[str]], search: str, limit: int, offset: int = 0):
    if not (1 <= limit <= 100) or not (0 <= offset <= 1000000):
        raise ContextualAutocompleteError("Invalid autocomplete page or limit.")
    search = search.strip()
    if len(search) > 100:
        raise ContextualAutocompleteError("Autocomplete search text is too long.")
    selected = normalize_filters(filters, target)
    # Mirror the existing organization-wise autocomplete requirement. A
    # request must not bypass the controlling filter by calling this API.
    catalog = _load_mapping(_WORKBOOK.stat().st_mtime_ns) if _WORKBOOK.exists() else {}
    report_vars = catalog.get(normalize_identifier(report), {})
    controller = next((name for name in ("employee_org", "org_name", "business_group")
                       if name in report_vars), None)
    if controller and normalize_identifier(target) != controller and controller not in selected:
        raise ContextualAutocompleteError(f"Select {controller} before using autocomplete for this report.")
    docket_virtual = normalize_identifier(report) == "docket_report"
    if docket_virtual and normalize_identifier(target) == "period_name":
        choices = {"year": "year_period", "month": "month_period", "week": "week_period"}
        kinds = selected.pop("period_type", [])
        if len(kinds) != 1 or kinds[0].lower() not in choices:
            raise ContextualAutocompleteError("Select one PERIOD TYPE before PERIOD NAME.")
        target_mapping = _lookup(report, choices[kinds[0].lower()])
    elif docket_virtual and normalize_identifier(target) == "period_type":
        # A new period type selection invalidates any previously selected name.
        selected.pop("period_name", None)
        # A Docket Report period type is virtual. Determine which physical
        # period columns have rows matching the other selected filters.
        selected = _virtual_docket_filters(report, selected, target)
        return _build_docket_type_query(report, selected, search, limit, offset)
    else:
        target_mapping = _lookup(report, target)
    if target_mapping["storage"] != "redis_variable":
        raise ContextualAutocompleteError("Autocomplete is unavailable for this variable.")
    table = target_mapping["table"]
    if docket_virtual:
        selected = _virtual_docket_filters(report, selected, target)
    clauses = [f"t.{target_mapping['column']} IS NOT NULL"]
    binds: dict[str, Any] = {}
    index = 0
    for name, values in selected.items():
        mapping = _lookup(report, name)
        if mapping["table"] != table:
            raise ContextualAutocompleteError("Selected filters do not share one approved report table.")
        # Do not infer a join between unrelated reporting tables.
        conditions = []
        for value in values:
            parameter = f"filter_{index}"
            index += 1
            conditions.append(f"t.{mapping['column']} = :{parameter}")
            binds[parameter] = value
        clauses.append("(" + " OR ".join(conditions) + ")")
    target_sql = f"TO_CHAR(t.{target_mapping['column']})"
    if search:
        # ! escapes LIKE wildcards in the user-supplied search string.
        safe_search = search.casefold().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        clauses.append(f"LOWER({target_sql}) LIKE :search ESCAPE '!'")
        binds["search"] = "%" + safe_search + "%"
    sql = (
        f"SELECT DISTINCT {target_sql} AS VALUE "
        f"FROM {_SCHEMA}.{table} t "
        f"WHERE {' AND '.join(clauses)} "
        f"ORDER BY VALUE OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY"
    )
    return sql, binds, selected


def _build_docket_type_query(report: str, selected: dict[str, list[str]],
                            search: str, limit: int, offset: int = 0):
    table = _lookup(report, "year_period")["table"]
    binds: dict[str, str] = {}
    predicates: list[str] = []
    index = 0
    for name, values in selected.items():
        mapping = _lookup(report, name)
        if mapping["table"] != table:
            raise ContextualAutocompleteError("Selected filters do not share one approved report table.")
        terms = []
        for value in values:
            key = f"filter_{index}"
            index += 1
            binds[key] = value
            terms.append(f"t.{mapping['column']} = :{key}")
        predicates.append("(" + " OR ".join(terms) + ")")
    sql_sections = []
    for label, logical in (("Year", "year_period"), ("Month", "month_period"), ("Week", "week_period")):
        if search and search.casefold() not in label.casefold():
            continue
        column = _lookup(report, logical)["column"]
        where = " AND ".join([f"t.{column} IS NOT NULL", *predicates])
        sql_sections.append(
            f"SELECT '{label}' AS VALUE FROM DUAL WHERE EXISTS "
            f"(SELECT 1 FROM {_SCHEMA}.{table} t WHERE {where})"
        )
    if not sql_sections:
        # Query with no possible match and a stable one-column result.
        return "SELECT 'Year' AS VALUE FROM DUAL WHERE 1=0", {}, selected
    return (
        "SELECT VALUE FROM (" + " UNION ".join(sql_sections) + ") "
        f"ORDER BY VALUE OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY"
    ), binds, selected



def query_oracle_values(*, report: str, target: str, filters: dict[str, list[str]],
                        search: str, limit: int, offset: int = 0) -> dict[str, Any]:
    """Read-only lookup invoked by the authenticated internal endpoint.

    Caching belongs exclusively to the Redis API, never this service.
    """
    import os

    sql, binds, _normalized = build_query(report, target, filters, search, limit, offset)
    connection = get_connection()
    cursor = None
    try:
        connection.call_timeout = int(os.getenv("CONTEXT_AUTOCOMPLETE_ORACLE_TIMEOUT_MS", "5000"))
        cursor = connection.cursor()
        cursor.execute(sql, binds)
        rows = cursor.fetchmany(limit + 1)
        values = list(dict.fromkeys(
            str(row[0]).strip() for row in rows
            if row[0] is not None and str(row[0]).strip()
        ))
        has_more = len(rows) > limit
        page = values[:limit]
        return {
            "values": page, "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None,
            "source": "oracle",
        }
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()
