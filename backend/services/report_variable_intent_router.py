"""Determine whether an Ask request contains valid report-scoped variables.

Uses the same two local workbook catalogs as the frontend's variable and
sidebar-option lists. Never checks live Redis or attempts SQL generation.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

logger = logging.getLogger(__name__)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MAPPING_FILE = _BACKEND_ROOT / "redis_mapping.xlsx"
_OPTIONS_FILE = _BACKEND_ROOT / "Roles and options.xlsx"


def _name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_present(item) for item in value)
    return True


def _index_workbook(path: Path, report_column: str, variable_column: str) -> dict[str, set[str]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if not workbook.sheetnames:
            raise ValueError(f"No worksheets in {path.name}")
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, None)
        if not headers:
            raise ValueError(f"No headers in {path.name}")
        positions = {_name(value): i for i, value in enumerate(headers) if _name(value)}
        if report_column not in positions or variable_column not in positions:
            raise ValueError(f"Missing report/variable headers in {path.name}")
        report_index = positions[report_column]
        variable_index = positions[variable_column]
        current_report = ""
        index: dict[str, set[str]] = {}
        for row in rows:
            report = _name(row[report_index]) if report_index < len(row) else ""
            if report:
                current_report = report  # Continuation rows have an empty report cell.
            variable = _name(row[variable_index]) if variable_index < len(row) else ""
            if current_report and variable:
                index.setdefault(current_report, set()).add(variable)
        return index
    finally:
        workbook.close()


@lru_cache(maxsize=8)
def _read_allowed_variables(
    mapping_path: str,
    mapping_mtime_ns: int,
    options_path: str,
    options_mtime_ns: int,
) -> dict[str, frozenset[str]]:
    # The mtimes are cache keys: changes to either workbook reload the lists.
    mapping = _index_workbook(Path(mapping_path), "report name", "variables")
    options = _index_workbook(Path(options_path), "report name", "variable name") if options_mtime_ns >= 0 else {}
    for report, names in options.items():
        mapping.setdefault(report, set()).update(names)
    return {report: frozenset(names) for report, names in mapping.items()}


def has_valid_report_variables(
    report_name: str | None,
    variables: list[dict[str, Any]] | None,
    *,
    mapping_path: Path | None = None,
    options_path: Path | None = None,
) -> bool:
    """True only if every supplied variable belongs to the selected report.

    Empty/missing inputs, absent reports, and unreadable mapping workbooks
    return False, retaining existing LLM classification instead of guessing.
    """
    report = _name(report_name)
    if not report or not variables:
        return False

    mapping = Path(mapping_path or _MAPPING_FILE)
    options = Path(options_path or _OPTIONS_FILE)
    try:
        mapping_mtime = mapping.stat().st_mtime_ns
        options_mtime = options.stat().st_mtime_ns if options.is_file() else -1
        allowed = _read_allowed_variables(
            str(mapping.resolve()), mapping_mtime,
            str(options.resolve()), options_mtime,
        ).get(report, frozenset())
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Unable to validate structured report variables: %s", exc)
        return False

    if not allowed:
        return False

    return all(
        isinstance(variable, dict)
        and _name(variable.get("name")) in allowed
        and _present(variable.get("value"))
        and _name(variable.get("source") or "input") in {"input", "option"}
        for variable in variables
    )
