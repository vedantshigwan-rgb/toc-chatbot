from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from openpyxl import load_workbook
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from contextual_cache_service import query_contextual_cached, ContextualLookupError
from suggestion_paging import slice_page

from autocomplete_config import get_controller_variable, get_period_dependency


# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

JSON_FOLDER = BASE_DIR / "json_outputs"

ROLES_OPTIONS_WORKBOOK = BASE_DIR / "Roles and options.xlsx"
ROLES_OPTIONS_SHEET = "Sheet2"

REDIS_MAPPING_WORKBOOK = BASE_DIR / "redis_mapping.xlsx"
REDIS_MAPPING_SHEET = "Sheet1"

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DATABASE = 0
REDIS_ROOT = "aeris"


# =============================================================================
# Runtime catalogs
# =============================================================================

redis_mapping_catalog: dict[str, Any] = {
    "source": "",
    "sheet": "",
    "report_count": 0,
    "reports": {},
    "warnings": [],
}

navigation_catalog: dict[str, Any] = {
    "source": "",
    "sheet": "",
    "base_tab_count": 0,
    "base_tabs": [],
    "warnings": [],
}


# =============================================================================
# Redis connection
# =============================================================================

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DATABASE,
    decode_responses=True,
)


# =============================================================================
# Normalization
# =============================================================================

_non_alphanumeric_pattern = re.compile(r"[^a-z0-9]+")
_multiple_spaces_pattern = re.compile(r"\s+")


def normalize(text: Any) -> str:
    if text is None:
        return ""

    value = str(text).lower()
    value = _non_alphanumeric_pattern.sub(" ", value)
    value = _multiple_spaces_pattern.sub(" ", value)

    return value.strip()


def clean_text(value: Any) -> str:
    """
    Clean workbook text and collapse accidental spacing.

    This is used for report names, headers, options, and
    variable names. It is not used for Redis variable values,
    so double spaces inside values remain preserved.
    """

    if value is None:
        return ""

    return _multiple_spaces_pattern.sub(
        " ",
        str(value).strip(),
    )


def make_identifier(text: Any) -> str:
    return normalize(text).replace(" ", "_")


def prefix_key(text: Any) -> str:
    return make_identifier(text)


def create_prefixes(text: Any) -> set[str]:
    """
    Create searchable prefixes for the complete value and
    for every individual word in the value.
    """

    normalized_text = normalize(text)

    if not normalized_text:
        return set()

    searchable_parts = {normalized_text}
    searchable_parts.update(normalized_text.split())

    prefixes: set[str] = set()

    for part in searchable_parts:
        for position in range(1, len(part) + 1):
            prefixes.add(
                prefix_key(
                    part[:position]
                )
            )

    return prefixes


# =============================================================================
# Redis key helpers
# =============================================================================

def report_names_key() -> str:
    return f"{REDIS_ROOT}:report_names"


def report_search_key(prefix: str) -> str:
    return (
        f"{REDIS_ROOT}:index:reports:"
        f"{prefix_key(prefix)}"
    )


def report_variables_key(report_id: str) -> str:
    return (
        f"{REDIS_ROOT}:report:"
        f"{report_id}:variables"
    )


def variable_names_key(report_id: str) -> str:
    return (
        f"{REDIS_ROOT}:report:"
        f"{report_id}:variable_names"
    )


def variable_value_search_key(
    report_id: str,
    variable_id: str,
    prefix: str,
) -> str:
    return (
        f"{REDIS_ROOT}:index:values:"
        f"{report_id}:"
        f"{variable_id}:"
        f"{prefix_key(prefix)}"
    )


def variable_all_values_key(
    report_id: str,
    variable_id: str,
) -> str:
    return (
        f"{REDIS_ROOT}:values:"
        f"{report_id}:"
        f"{variable_id}:all"
    )


def controller_bucket_id(value: Any) -> str:
    """Stable compact Redis-key component for one exact controller value."""
    raw = str(value or "").strip().casefold().encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:20]


def scoped_variable_value_search_key(
    report_id: str,
    controller_variable_id: str,
    controller_value: str,
    variable_id: str,
    prefix: str,
) -> str:
    return (
        f"{REDIS_ROOT}:index:scoped_values:"
        f"{report_id}:"
        f"{controller_variable_id}:"
        f"{controller_bucket_id(controller_value)}:"
        f"{variable_id}:"
        f"{prefix_key(prefix)}"
    )


def scoped_variable_all_values_key(
    report_id: str,
    controller_variable_id: str,
    controller_value: str,
    variable_id: str,
) -> str:
    return (
        f"{REDIS_ROOT}:scoped_values:"
        f"{report_id}:"
        f"{controller_variable_id}:"
        f"{controller_bucket_id(controller_value)}:"
        f"{variable_id}:all"
    )


# =============================================================================
# Dependent period dropdown keys (organization -> period type -> period name)
# =============================================================================

def period_types_key(report_id: str, org_value: str) -> str:
    return (
        f"{REDIS_ROOT}:periods:{report_id}:"
        f"{controller_bucket_id(org_value)}:types"
    )


def period_names_key(report_id: str, org_value: str, period_type: str) -> str:
    return (
        f"{REDIS_ROOT}:periods:{report_id}:"
        f"{controller_bucket_id(org_value)}:"
        f"{controller_bucket_id(period_type)}:names"
    )


# =============================================================================
# redis_mapping workbook
# =============================================================================

def load_redis_mapping_catalog() -> dict[str, Any]:
    """
    Read redis_mapping.xlsx / Sheet1.

    Required columns:

        report name
        variables
        column names
        table name
        storage_type

    The returned catalog is indexed using normalized report,
    logical-variable, and physical-column identifiers.
    """

    if not REDIS_MAPPING_WORKBOOK.exists():
        raise FileNotFoundError(
            "Mapping workbook not found: "
            f"{REDIS_MAPPING_WORKBOOK}"
        )

    workbook = load_workbook(
        REDIS_MAPPING_WORKBOOK,
        read_only=True,
        data_only=True,
    )

    try:
        if REDIS_MAPPING_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Sheet '{REDIS_MAPPING_SHEET}' was not found "
                f"in {REDIS_MAPPING_WORKBOOK.name}. "
                f"Available sheets: {workbook.sheetnames}"
            )

        worksheet = workbook[
            REDIS_MAPPING_SHEET
        ]

        header_row = next(
            worksheet.iter_rows(
                min_row=1,
                max_row=1,
                values_only=True,
            ),
            None,
        )

        if not header_row:
            raise ValueError(
                "Mapping workbook has no header row"
            )

        header_indexes: dict[str, int] = {}

        for index, header in enumerate(
            header_row
        ):
            normalized_header = normalize(
                header
            )

            if normalized_header:
                header_indexes[
                    normalized_header
                ] = index

        required_headers = {
            "report name",
            "variables",
            "column names",
            "table name",
            "storage type",
        }

        missing_headers = sorted(
            required_headers
            - set(header_indexes)
        )

        if missing_headers:
            raise ValueError(
                "Mapping workbook is missing required "
                f"headers: {missing_headers}. "
                f"Found: {sorted(header_indexes)}"
            )

        reports: dict[
            str,
            dict[str, Any],
        ] = {}

        warnings: list[
            dict[str, Any]
        ] = []

        current_report_name = ""

        for row_number, row in enumerate(
            worksheet.iter_rows(
                min_row=2,
                values_only=True,
            ),
            start=2,
        ):
            def cell(
                header_name: str,
            ) -> str:
                index = header_indexes[
                    header_name
                ]

                if index >= len(row):
                    return ""

                return clean_text(
                    row[index]
                )

            report_name = cell(
                "report name"
            )

            variable_name = cell(
                "variables"
            )

            column_name = cell(
                "column names"
            )

            table_name = cell(
                "table name"
            )

            storage_type = cell(
                "storage type"
            )

            # Support blank or merged report-name cells.
            if report_name:
                current_report_name = (
                    report_name
                )
            else:
                report_name = (
                    current_report_name
                )

            if not any(
                [
                    report_name,
                    variable_name,
                    column_name,
                    table_name,
                    storage_type,
                ]
            ):
                continue

            if not report_name:
                warnings.append(
                    {
                        "row": row_number,
                        "type": (
                            "missing_report_name"
                        ),
                        "message": (
                            "Mapping row has no "
                            "report name."
                        ),
                    }
                )
                continue

            if not variable_name:
                warnings.append(
                    {
                        "row": row_number,
                        "type": (
                            "missing_variable_name"
                        ),
                        "report_name": (
                            report_name
                        ),
                        "message": (
                            "Mapping row has no "
                            "variable name."
                        ),
                    }
                )
                continue

            if not column_name:
                warnings.append(
                    {
                        "row": row_number,
                        "type": (
                            "missing_column_name"
                        ),
                        "report_name": (
                            report_name
                        ),
                        "variable_name": (
                            variable_name
                        ),
                        "message": (
                            "Mapping row has no "
                            "column name."
                        ),
                    }
                )
                continue

            report_id = make_identifier(
                report_name
            )

            variable_id = make_identifier(
                variable_name
            )

            column_id = make_identifier(
                column_name
            )

            if (
                not report_id
                or not variable_id
                or not column_id
            ):
                continue

            report_entry = reports.setdefault(
                report_id,
                {
                    "id": report_id,
                    "name": report_name,
                    "variables": {},
                    "columns": {},
                    "table_names": set(),
                },
            )

            if table_name:
                report_entry[
                    "table_names"
                ].add(
                    table_name
                )

            normalized_storage_type = (
                normalize(
                    storage_type
                )
            )

            # Only this exact normalized value supports
            # autocomplete.
            #
            # "Non-redis variable"
            # "Non- redis variable"
            #
            # both normalize to "non redis variable",
            # so they correctly evaluate as False.
            is_redis_variable = (
                normalized_storage_type
                == "redis variable"
            )

            variable_entry = {
                "variable_name": (
                    variable_name
                ),
                "column_name": (
                    column_name
                ),
                "table_name": (
                    table_name
                ),
                "storage_type": (
                    storage_type
                ),
                "is_redis_variable": (
                    is_redis_variable
                ),
                "source_row": (
                    row_number
                ),
            }

            existing = report_entry[
                "variables"
            ].get(
                variable_id
            )

            if (
                existing
                and make_identifier(
                    existing[
                        "column_name"
                    ]
                )
                != column_id
            ):
                warnings.append(
                    {
                        "row": row_number,
                        "type": (
                            "duplicate_variable_mapping"
                        ),
                        "report_name": (
                            report_name
                        ),
                        "variable_name": (
                            variable_name
                        ),
                        "existing_column": (
                            existing[
                                "column_name"
                            ]
                        ),
                        "new_column": (
                            column_name
                        ),
                    }
                )

            report_entry[
                "variables"
            ][variable_id] = (
                variable_entry
            )

            report_entry[
                "columns"
            ][column_id] = (
                variable_entry
            )

        for report_entry in reports.values():
            report_entry[
                "table_names"
            ] = sorted(
                report_entry[
                    "table_names"
                ],
                key=str.casefold,
            )

        result = {
            "source": (
                REDIS_MAPPING_WORKBOOK.name
            ),
            "sheet": (
                REDIS_MAPPING_SHEET
            ),
            "report_count": (
                len(reports)
            ),
            "reports": (
                reports
            ),
            "warnings": (
                warnings
            ),
        }

        logger.info(
            "Loaded mapping workbook: "
            "%s reports, %s warnings",
            len(reports),
            len(warnings),
        )

        return result

    finally:
        workbook.close()


def find_mapping_report(
    report_name: str,
) -> dict[str, Any] | None:
    """
    Find a mapping report using its normalized report name.
    """

    report_id = make_identifier(
        report_name
    )

    return redis_mapping_catalog.get(
        "reports",
        {},
    ).get(
        report_id
    )


def find_mapping_report_by_table(
    table_name: str,
) -> dict[str, Any] | None:
    """
    Fall back to matching a report using its table name.

    This supports development differences such as:

        Roles workbook:
            Docket Employee Report

        Mapping workbook:
            Docket Emp Report

    where both rows use the same table.
    """

    table_id = make_identifier(
        table_name
    )

    if not table_id:
        return None

    matches: list[
        dict[str, Any]
    ] = []

    for report_entry in (
        redis_mapping_catalog
        .get(
            "reports",
            {},
        )
        .values()
    ):
        report_table_ids = {
            make_identifier(name)
            for name
            in report_entry.get(
                "table_names",
                [],
            )
        }

        if table_id in report_table_ids:
            matches.append(
                report_entry
            )

    # Do not guess when multiple reports use the same table.
    if len(matches) == 1:
        return matches[0]

    return None


def find_mapping_variable(
    report_entry: dict[str, Any],
    variable_or_column_name: str,
) -> dict[str, Any] | None:
    """
    Resolve a variable by either:

        logical variable name
        physical column name
    """

    identifier = make_identifier(
        variable_or_column_name
    )

    if not identifier:
        return None

    return (
        report_entry
        .get(
            "variables",
            {},
        )
        .get(
            identifier
        )
        or
        report_entry
        .get(
            "columns",
            {},
        )
        .get(
            identifier
        )
    )


# =============================================================================
# Roles and options workbook
# =============================================================================

def build_navigation_hierarchy() -> dict[str, Any]:
    """
    Read Roles and options.xlsx / Sheet2.

    Expected columns:

        A: Base tabs
        B: Sub tab
        C: Report name
        D: Table name
        E: Options
        F: Variable name

    Returned hierarchy:

        base_tabs
            -> sub_tabs
                -> reports
                    -> options
                        -> variable_name

    Blank cells continue the value from the previous row.

    Example:

        OA Trolley Status
            OA Trolley Report
            Buyerwise OA Trolley Report

    Both report rows remain under the same sub-tab.
    """

    if not ROLES_OPTIONS_WORKBOOK.exists():
        raise FileNotFoundError(
            "Roles/options workbook not found: "
            f"{ROLES_OPTIONS_WORKBOOK}"
        )

    workbook = load_workbook(
        ROLES_OPTIONS_WORKBOOK,
        read_only=True,
        data_only=True,
    )

    try:
        if ROLES_OPTIONS_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Sheet '{ROLES_OPTIONS_SHEET}' was not found "
                f"in {ROLES_OPTIONS_WORKBOOK.name}. "
                f"Available sheets: {workbook.sheetnames}"
            )

        worksheet = workbook[
            ROLES_OPTIONS_SHEET
        ]

        base_tabs: list[dict[str, Any]] = []

        base_tab_lookup: dict[
            str,
            dict[str, Any],
        ] = {}

        sub_tab_lookup: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        report_lookup: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}

        warnings: list[dict[str, Any]] = []

        current_base_tab: dict[str, Any] | None = None
        current_sub_tab: dict[str, Any] | None = None
        current_report: dict[str, Any] | None = None

        for row_number, row in enumerate(
            worksheet.iter_rows(
                min_row=2,
                max_col=6,
                values_only=True,
            ),
            start=2,
        ):
            (
                base_tab_cell,
                sub_tab_cell,
                report_name_cell,
                table_name_cell,
                option_cell,
                variable_name_cell,
            ) = row

            base_tab_label = clean_text(
                base_tab_cell
            )

            sub_tab_label = clean_text(
                sub_tab_cell
            )

            report_name = clean_text(
                report_name_cell
            )

            table_name = clean_text(
                table_name_cell
            )

            option_label = clean_text(
                option_cell
            )

            roles_variable_name = clean_text(
                variable_name_cell
            )

            if not any(
                [
                    base_tab_label,
                    sub_tab_label,
                    report_name,
                    table_name,
                    option_label,
                    roles_variable_name,
                ]
            ):
                continue

            # =================================================
            # Base tab
            # =================================================

            if base_tab_label:
                base_tab_id = make_identifier(
                    base_tab_label
                )

                current_base_tab = (
                    base_tab_lookup.get(
                        base_tab_id
                    )
                )

                if current_base_tab is None:
                    current_base_tab = {
                        "id": base_tab_id,
                        "label": base_tab_label,
                        "sub_tabs": [],
                    }

                    base_tab_lookup[
                        base_tab_id
                    ] = current_base_tab

                    base_tabs.append(
                        current_base_tab
                    )

                current_sub_tab = None
                current_report = None

            elif current_base_tab is None:
                warnings.append(
                    {
                        "row": row_number,
                        "type": "missing_base_tab",
                        "message": (
                            "The row has no base-tab context."
                        ),
                    }
                )

                continue

            # =================================================
            # Sub tab
            # =================================================

            if sub_tab_label:
                sub_tab_identifier = make_identifier(
                    sub_tab_label
                )

                sub_tab_key = (
                    current_base_tab["id"],
                    sub_tab_identifier,
                )

                current_sub_tab = (
                    sub_tab_lookup.get(
                        sub_tab_key
                    )
                )

                if current_sub_tab is None:
                    current_sub_tab = {
                        "id": (
                            f"{current_base_tab['id']}__"
                            f"{sub_tab_identifier}"
                        ),
                        "label": sub_tab_label,
                        "reports": [],
                    }

                    sub_tab_lookup[
                        sub_tab_key
                    ] = current_sub_tab

                    current_base_tab[
                        "sub_tabs"
                    ].append(
                        current_sub_tab
                    )

                current_report = None

            elif current_sub_tab is None:
                warnings.append(
                    {
                        "row": row_number,
                        "type": "missing_sub_tab",
                        "base_tab": (
                            current_base_tab["label"]
                        ),
                        "message": (
                            "The row has no sub-tab context."
                        ),
                    }
                )

                continue

            # =================================================
            # Report
            # =================================================

            if report_name:
                mapping_report = find_mapping_report(
                    report_name
                )

                if (
                    mapping_report is None
                    and table_name
                ):
                    mapping_report = (
                        find_mapping_report_by_table(
                            table_name
                        )
                    )

                autocomplete_report_name = (
                    mapping_report["name"]
                    if mapping_report
                    else report_name
                )

                report_identifier = make_identifier(
                    report_name
                )

                table_identifier = make_identifier(
                    table_name
                )

                report_key = (
                    current_sub_tab["id"],
                    report_identifier,
                    table_identifier,
                )

                current_report = (
                    report_lookup.get(
                        report_key
                    )
                )

                if current_report is None:
                    current_report = {
                        "id": (
                            f"{current_sub_tab['id']}__"
                            f"{report_identifier}"
                        ),

                        # Displayed inside the Report dropdown.
                        "label": report_name,

                        # Sent to /ask later.
                        "report_name": report_name,

                        # Used only by autocomplete.
                        "autocomplete_report_name": (
                            autocomplete_report_name
                        ),

                        # Kept as metadata but not displayed.
                        "table_name": table_name,

                        "options": [],
                    }

                    report_lookup[
                        report_key
                    ] = current_report

                    current_sub_tab[
                        "reports"
                    ].append(
                        current_report
                    )

                if mapping_report is None:
                    warnings.append(
                        {
                            "row": row_number,
                            "type": (
                                "report_not_found_in_mapping"
                            ),
                            "report_name": report_name,
                            "table_name": table_name,
                            "message": (
                                "The report remains available "
                                "for navigation, but Redis "
                                "autocomplete metadata could "
                                "not be resolved."
                            ),
                        }
                    )

            elif current_report is None:
                warnings.append(
                    {
                        "row": row_number,
                        "type": "missing_report_context",
                        "base_tab": (
                            current_base_tab["label"]
                        ),
                        "sub_tab": (
                            current_sub_tab["label"]
                        ),
                        "message": (
                            "The option could not be attached "
                            "because no report was resolved."
                        ),
                    }
                )

                continue

            # =================================================
            # Option and associated variable
            # =================================================

            if option_label or roles_variable_name:
                if not roles_variable_name:
                    warnings.append(
                        {
                            "row": row_number,
                            "type": (
                                "missing_variable_name"
                            ),
                            "report_name": (
                                current_report[
                                    "report_name"
                                ]
                            ),
                            "option": option_label,
                        }
                    )

                    continue

                mapping_report = find_mapping_report(
                    current_report[
                        "autocomplete_report_name"
                    ]
                )

                mapping_variable = None

                if mapping_report:
                    mapping_variable = (
                        find_mapping_variable(
                            mapping_report,
                            roles_variable_name,
                        )
                    )

                if mapping_variable:
                    payload_variable_name = (
                        mapping_variable[
                            "variable_name"
                        ]
                    )

                    column_name = (
                        mapping_variable[
                            "column_name"
                        ]
                    )

                    storage_type = (
                        mapping_variable[
                            "storage_type"
                        ]
                    )

                    autocomplete_enabled = (
                        mapping_variable[
                            "is_redis_variable"
                        ]
                    )

                else:
                    payload_variable_name = (
                        roles_variable_name
                    )

                    column_name = (
                        roles_variable_name
                    )

                    storage_type = ""

                    autocomplete_enabled = False

                    warnings.append(
                        {
                            "row": row_number,
                            "type": (
                                "variable_not_found_in_mapping"
                            ),
                            "report_name": (
                                current_report[
                                    "report_name"
                                ]
                            ),
                            "variable_name": (
                                roles_variable_name
                            ),
                            "message": (
                                "The option remains selectable, "
                                "but Redis autocomplete is "
                                "disabled."
                            ),
                        }
                    )

                option_identifier = make_identifier(
                    payload_variable_name
                )

                option_object = {
                    "id": (
                        f"{current_report['id']}__"
                        f"{option_identifier}"
                    ),

                    # Displayed inside the Option dropdown.
                    "label": (
                        option_label
                        or payload_variable_name
                    ),

                    # Used later in variables[].name.
                    "variable_name": (
                        payload_variable_name
                    ),

                    # Physical database/Redis column.
                    "column_name": column_name,

                    "storage_type": storage_type,

                    # Only true for Redis-variable mappings.
                    "autocomplete_enabled": (
                        autocomplete_enabled
                    ),
                }

                option_already_exists = any(
                    make_identifier(
                        existing_option[
                            "variable_name"
                        ]
                    )
                    == option_identifier
                    for existing_option
                    in current_report[
                        "options"
                    ]
                )

                if not option_already_exists:
                    current_report[
                        "options"
                    ].append(
                        option_object
                    )

        result = {
            "source": (
                ROLES_OPTIONS_WORKBOOK.name
            ),
            "sheet": (
                ROLES_OPTIONS_SHEET
            ),
            "base_tab_count": len(
                base_tabs
            ),
            "base_tabs": base_tabs,
            "warning_count": len(
                warnings
            ),
            "warnings": warnings,
        }

        logger.info(
            "Loaded navigation: %s base tabs, "
            "%s warnings",
            len(base_tabs),
            len(warnings),
        )

        return result

    finally:
        workbook.close()

def find_navigation_report_by_report_name(
    report_name: str,
) -> dict[str, Any] | None:
    """
    Find a report inside the navigation hierarchy using
    either its /ask report name or autocomplete report name.
    """

    report_id = make_identifier(
        report_name
    )

    for base_tab in navigation_catalog.get(
        "base_tabs",
        [],
    ):
        for sub_tab in base_tab.get(
            "sub_tabs",
            [],
        ):
            for report in sub_tab.get(
                "reports",
                [],
            ):
                ask_report_id = make_identifier(
                    report.get(
                        "report_name",
                        "",
                    )
                )

                autocomplete_report_id = (
                    make_identifier(
                        report.get(
                            "autocomplete_report_name",
                            "",
                        )
                    )
                )

                if report_id in {
                    ask_report_id,
                    autocomplete_report_id,
                }:
                    return report

    return None

def resolve_mapping_report_for_any_name(
    report_name: str,
) -> dict[str, Any] | None:
    """
    Resolve a mapping report using:

        1. The mapping workbook report name.
        2. A report alias from Roles and options.xlsx.
        3. The report's table name.
    """

    mapping_report = find_mapping_report(
        report_name
    )

    if mapping_report:
        return mapping_report

    navigation_report = (
        find_navigation_report_by_report_name(
            report_name
        )
    )

    if not navigation_report:
        return None

    autocomplete_report_name = (
        navigation_report.get(
            "autocomplete_report_name",
            "",
        )
    )

    mapping_report = find_mapping_report(
        autocomplete_report_name
    )

    if mapping_report:
        return mapping_report

    return find_mapping_report_by_table(
        navigation_report.get(
            "table_name",
            "",
        )
    )
# =============================================================================
# Redis snapshot loading
# =============================================================================

def delete_old_snapshot() -> int:
    """
    Delete only this application's aeris:* keys.
    """

    deleted_count = 0
    keys_to_delete: list[str] = []

    for key in redis_client.scan_iter(
        match=f"{REDIS_ROOT}:*"
    ):
        keys_to_delete.append(
            key
        )

        if len(keys_to_delete) >= 500:
            deleted_count += (
                redis_client.delete(
                    *keys_to_delete
                )
            )

            keys_to_delete.clear()

    if keys_to_delete:
        deleted_count += (
            redis_client.delete(
                *keys_to_delete
            )
        )

    logger.info(
        "Deleted %s previous Redis keys",
        deleted_count,
    )

    return deleted_count


def load_snapshot() -> dict[str, int]:
    """
    Load JSON autocomplete values into Redis.

    New JSON files generated by gene.py contain one logical variable/value
    plus an optional controlling-variable value.

    Controller variable values are stored globally for the report.
    Dependent variable values are stored ONLY inside the controller bucket.
    This means selecting one org (or other configured controller) limits every
    other Redis autocomplete variable to values belonging to that controller.
    """

    if not JSON_FOLDER.exists():
        raise FileNotFoundError(
            "JSON folder does not exist: "
            f"{JSON_FOLDER}"
        )

    redis_client.ping()
    delete_old_snapshot()

    json_files = sorted(JSON_FOLDER.glob("*.json"))

    total_rows = 0
    total_values = 0
    skipped_rows = 0
    failed_files = 0

    pipeline = redis_client.pipeline(transaction=False)
    pending_commands = 0

    def flush(force: bool = False) -> None:
        nonlocal pipeline
        nonlocal pending_commands

        if pending_commands and (force or pending_commands >= 5000):
            pipeline.execute()
            pipeline = redis_client.pipeline(transaction=False)
            pending_commands = 0

    def queue_value(
        *,
        raw_report_name: str,
        raw_variable_name: str,
        raw_value: Any,
        controller_variable_name: str = "",
        controller_value: str = "",
        period_type: str = "",
    ) -> bool:
        nonlocal pending_commands
        nonlocal total_values

        mapping_report = resolve_mapping_report_for_any_name(raw_report_name)

        report_name = (
            mapping_report["name"]
            if mapping_report
            else clean_text(raw_report_name)
        )
        report_id = make_identifier(report_name)

        if not report_id:
            return False

        mapping_variable = None
        if mapping_report:
            mapping_variable = find_mapping_variable(
                mapping_report,
                raw_variable_name,
            )

        if mapping_variable and not mapping_variable["is_redis_variable"]:
            return False

        logical_variable_name = (
            mapping_variable["variable_name"]
            if mapping_variable
            else clean_text(raw_variable_name)
        )
        redis_column_name = (
            mapping_variable["column_name"]
            if mapping_variable
            else clean_text(raw_variable_name)
        )
        variable_id = make_identifier(redis_column_name)

        value = "" if raw_value is None else str(raw_value).strip()
        if not variable_id or not value:
            return False

        configured_controller = get_controller_variable(report_name)
        configured_controller_id = make_identifier(configured_controller)
        logical_variable_id = make_identifier(logical_variable_name)

        pipeline.hset(report_names_key(), report_id, report_name)
        pending_commands += 1

        for report_prefix in create_prefixes(report_name):
            pipeline.sadd(report_search_key(report_prefix), report_id)
            pending_commands += 1

        pipeline.sadd(report_variables_key(report_id), variable_id)
        pipeline.hset(
            variable_names_key(report_id),
            variable_id,
            redis_column_name,
        )
        pending_commands += 2

        # Controller itself (or an unconfigured report) keeps global values.
        is_controller = bool(
            configured_controller_id
            and logical_variable_id == configured_controller_id
        )

        if not configured_controller or is_controller:
            pipeline.sadd(
                variable_all_values_key(report_id, variable_id),
                value,
            )
            pending_commands += 1

            for value_prefix in create_prefixes(value):
                pipeline.sadd(
                    variable_value_search_key(
                        report_id,
                        variable_id,
                        value_prefix,
                    ),
                    value,
                )
                pending_commands += 1

            total_values += 1
            flush()
            return True

        # Every non-controller variable in a configured report MUST have the
        # parent/controller value produced by gene.py.
        effective_controller_name = (
            clean_text(controller_variable_name)
            or configured_controller
        )

        if make_identifier(effective_controller_name) != configured_controller_id:
            return False

        cleaned_controller_value = str(controller_value or "").strip()
        if not cleaned_controller_value:
            return False

        dependency = get_period_dependency(report_name)
        if dependency:
            period_name_id = make_identifier(dependency["period_name_variable"])
            period_type_id = make_identifier(dependency["period_type_variable"])
            if logical_variable_id == period_name_id:
                selected_type = str(period_type or "").strip()
                if not selected_type:
                    # Old JSON without PERIOD_TYPE cannot provide safe cascading.
                    return False
                pipeline.sadd(
                    period_names_key(report_id, cleaned_controller_value, selected_type),
                    value,
                )
                pending_commands += 1
                total_values += 1
                flush()
                return True
            if logical_variable_id == period_type_id:
                pipeline.sadd(
                    period_types_key(report_id, cleaned_controller_value),
                    value,
                )
                pending_commands += 1

        pipeline.sadd(
            scoped_variable_all_values_key(
                report_id,
                configured_controller_id,
                cleaned_controller_value,
                variable_id,
            ),
            value,
        )
        pending_commands += 1

        for value_prefix in create_prefixes(value):
            pipeline.sadd(
                scoped_variable_value_search_key(
                    report_id,
                    configured_controller_id,
                    cleaned_controller_value,
                    variable_id,
                    value_prefix,
                ),
                value,
            )
            pending_commands += 1

        total_values += 1
        flush()
        return True

    for json_file in json_files:
        logger.info("Processing JSON: %s", json_file.name)

        try:
            with json_file.open("r", encoding="utf-8") as file:
                rows = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            failed_files += 1
            logger.exception("Could not read %s: %s", json_file, error)
            continue

        if isinstance(rows, dict):
            rows = [rows]

        if not isinstance(rows, list):
            failed_files += 1
            continue

        for row in rows:
            total_rows += 1

            if not isinstance(row, dict):
                skipped_rows += 1
                continue

            raw_report_name = clean_text(row.get("REPORT_NAME"))
            if not raw_report_name:
                skipped_rows += 1
                continue

            # New standardized JSON shape from the updated gene.py.
            if "VARIABLE_NAME" in row and "VALUE" in row:
                stored = queue_value(
                    raw_report_name=raw_report_name,
                    raw_variable_name=clean_text(row.get("VARIABLE_NAME")),
                    raw_value=row.get("VALUE"),
                    controller_variable_name=clean_text(
                        row.get("CONTROLLER_VARIABLE")
                    ),
                    controller_value=clean_text(row.get("CONTROLLER_VALUE")),
                    period_type=clean_text(row.get("PERIOD_TYPE")),
                )

                if not stored:
                    skipped_rows += 1
                continue

            # Backward compatibility for older JSON files. These can only
            # safely populate global/unconfigured values. Regenerate JSON with
            # the new gene.py before using scoped autocomplete.
            stored_any = False
            for raw_variable_name, raw_value in row.items():
                if raw_variable_name == "REPORT_NAME" or raw_value is None:
                    continue

                if queue_value(
                    raw_report_name=raw_report_name,
                    raw_variable_name=raw_variable_name,
                    raw_value=raw_value,
                ):
                    stored_any = True

            if not stored_any:
                skipped_rows += 1

    flush(force=True)

    result = {
        "json_files": len(json_files),
        "failed_files": failed_files,
        "rows": total_rows,
        "stored_values": total_values,
        "skipped_rows": skipped_rows,
    }

    logger.info("Redis loading completed: %s", result)
    return result


# =============================================================================
# FastAPI lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    global redis_mapping_catalog
    global navigation_catalog

    try:
        redis_client.ping()

        logger.info(
            "Connected to Redis at %s:%s",
            REDIS_HOST,
            REDIS_PORT,
        )

        # The mapping must load before navigation because
        # navigation options are enriched from the mapping.
        redis_mapping_catalog = (
            load_redis_mapping_catalog()
        )

        navigation_catalog = (
            build_navigation_hierarchy()
        )

        # Navigation aliases are now available while loading
        # development JSON report names into Redis.
        load_snapshot()

    except Exception:
        logger.exception(
            "Application startup failed"
        )

        raise

    yield

    redis_client.close()


# =============================================================================
# FastAPI application
# =============================================================================

app = FastAPI(
    title=(
        "Report Navigation and "
        "Variable Autocomplete API"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=[
        "GET",
        "POST",
        "OPTIONS",
    ],
    allow_headers=["*"],
)


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "message": (
            "Navigation and autocomplete "
            "API is running"
        ),
        "endpoints": {
            "navigation": (
                "/navigation"
            ),
            "catalog": (
                "/catalog"
            ),
            "variables": (
                "/variables?"
                "report_name=Inventory Report"
            ),
            "autocomplete": (
                "/autocomplete?"
                "report_name=Inventory Report"
                "&variable_name=buyer_name"
                "&q=p"
                "&limit=20"
            ),
            "period_options": (
                "/period-options?report_name=Docket%20Employee%20Report"
                "&org_value=ACD%20-%20PRODUCTS&period_type=Year"
            ),
            "reload": (
                "/reload"
            ),
            "docs": (
                "/docs"
            ),
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    redis_client.ping()

    return {
        "status": "healthy",
        "mapping_reports": (
            redis_mapping_catalog.get(
                "report_count",
                0,
            )
        ),
        "base_tabs": (
            navigation_catalog.get(
                "base_tab_count",
                0,
            )
        ),
    }


@app.get("/navigation")
def get_navigation() -> dict[str, Any]:
    """
    Return the frontend sidebar hierarchy.

    The response structure is:

        base_tabs[]
            sub_tabs[]
                report_name
                table_name
                options[]
    """

    return navigation_catalog


@app.get("/reports")
def search_reports(
    q: str = Query(
        ...,
        min_length=1,
    ),
    limit: int = Query(
        20,
        ge=1,
        le=200,
    ),
) -> dict[str, Any]:
    """
    Search reports from redis_mapping.xlsx.
    """

    normalized_query = normalize(
        q
    )

    reports: list[str] = []

    for report_entry in (
        redis_mapping_catalog
        .get(
            "reports",
            {},
        )
        .values()
    ):
        report_name = report_entry[
            "name"
        ]

        normalized_name = normalize(
            report_name
        )

        matches = (
            normalized_name.startswith(
                normalized_query
            )
            or any(
                word.startswith(
                    normalized_query
                )
                for word
                in normalized_name.split()
            )
        )

        if matches:
            reports.append(
                report_name
            )

    reports = sorted(
        set(reports),
        key=str.casefold,
    )

    limited_reports = reports[
        :limit
    ]

    return {
        "query": q,
        "count": (
            len(limited_reports)
        ),
        "total_matches": (
            len(reports)
        ),
        "reports": (
            limited_reports
        ),
    }


@app.get("/variables")
def get_report_variables(
    report_name: str = Query(
        ...,
        min_length=1,
    ),
) -> dict[str, Any]:
    """
    Return every mapping variable for a report.

    This endpoint includes both Redis and non-Redis variables.
    """

    mapping_report = (
        resolve_mapping_report_for_any_name(
            report_name
        )
    )

    if mapping_report is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Report not found in mapping: "
                f"{report_name}"
            ),
        )

    variable_details = sorted(
        mapping_report[
            "variables"
        ].values(),
        key=lambda item: (
            item[
                "variable_name"
            ].casefold()
        ),
    )

    return {
        "requested_report_name": (
            report_name
        ),
        "report_name": (
            mapping_report[
                "name"
            ]
        ),
        "autocomplete_controller": (
            (lambda controller_name: (
                {
                    "variable_name": controller_name,
                    "required_for_dependent_variables": True,
                }
                if controller_name
                else None
            ))(
                get_controller_variable(
                    mapping_report["name"]
                )
            )
        ),
        "count": (
            len(variable_details)
        ),
        "variables": [
            item[
                "variable_name"
            ]
            for item
            in variable_details
        ],
        "variable_details": [
            {
                "variable_name": (
                    item[
                        "variable_name"
                    ]
                ),
                "column_name": (
                    item[
                        "column_name"
                    ]
                ),
                "table_name": (
                    item[
                        "table_name"
                    ]
                ),
                "storage_type": (
                    item[
                        "storage_type"
                    ]
                ),
                "autocomplete_enabled": (
                    item[
                        "is_redis_variable"
                    ]
                ),
            }
            for item
            in variable_details
        ],
    }


@app.get("/catalog")
def get_catalog() -> dict[str, Any]:
    """
    Return all mapping reports and their variables.
    """

    reports: list[
        dict[str, Any]
    ] = []

    for mapping_report in (
        redis_mapping_catalog
        .get(
            "reports",
            {},
        )
        .values()
    ):
        variable_details = sorted(
            mapping_report[
                "variables"
            ].values(),
            key=lambda item: (
                item[
                    "variable_name"
                ].casefold()
            ),
        )

        reports.append(
            {
                "id": (
                    mapping_report[
                        "id"
                    ]
                ),
                "name": (
                    mapping_report[
                        "name"
                    ]
                ),
                "variables": [
                    item[
                        "variable_name"
                    ]
                    for item
                    in variable_details
                ],
                "variable_details": [
                    {
                        "variable_name": (
                            item[
                                "variable_name"
                            ]
                        ),
                        "column_name": (
                            item[
                                "column_name"
                            ]
                        ),
                        "table_name": (
                            item[
                                "table_name"
                            ]
                        ),
                        "storage_type": (
                            item[
                                "storage_type"
                            ]
                        ),
                        "autocomplete_enabled": (
                            item[
                                "is_redis_variable"
                            ]
                        ),
                    }
                    for item
                    in variable_details
                ],
            }
        )

    reports.sort(
        key=lambda item: (
            item[
                "name"
            ].casefold()
        )
    )

    return {
        "source": (
            REDIS_MAPPING_WORKBOOK.name
        ),
        "count": (
            len(reports)
        ),
        "reports": (
            reports
        ),
    }


def _get_period_options(
    report_name: str,
    org_value: str,
    period_type: str = "",
    q: str = "",
    limit: int = 40,
    offset: int = 0,
) -> dict[str, Any]:
    mapping_report = resolve_mapping_report_for_any_name(report_name)
    if mapping_report is None:
        raise HTTPException(404, detail=f"Report not found: {report_name}")
    dependency = get_period_dependency(mapping_report["name"])
    if dependency is None:
        raise HTTPException(400, detail="Period dropdown is not configured for this report")
    org = str(org_value or "").strip()
    if not org:
        raise HTTPException(400, detail=f"Select {dependency['controller']} first")

    report_id = mapping_report["id"]
    available_types = redis_client.smembers(period_types_key(report_id, org))
    selected_type = str(period_type or "").strip()
    if selected_type:
        canonical_type = next(
            (v for v in available_types if v.casefold() == selected_type.casefold()),
            None,
        )
        if canonical_type is None:
            raise HTTPException(400, detail="Period type is unavailable for this organization")
        candidate_values = redis_client.smembers(
            period_names_key(report_id, org, canonical_type)
        )
        variable = "period_name"
    else:
        candidate_values = available_types
        canonical_type = None
        variable = "period_type"

    query = normalize(q)
    values = sorted(
        (v for v in candidate_values if not query or normalize(v).startswith(query)),
        key=str.casefold,
    )
    return {
        "report_name": mapping_report["name"],
        "controller_variable_name": dependency["controller"],
        "controller_value": org,
        "variable_name": variable,
        "period_type": canonical_type,
        **slice_page(values, limit=limit, offset=offset),
    }


class ContextualRequest(BaseModel):
    report_name: str = Field(min_length=1, max_length=120)
    target_variable: str = Field(min_length=1, max_length=100)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    search_text: str = Field(default="", max_length=100)
    limit: int = Field(default=40, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)


@app.post("/autocomplete/contextual")
def contextual_autocomplete(
    payload: ContextualRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Cache-first contextual lookup; Oracle fallback is handled by TOC backend.

    Existing /autocomplete and /period-options remain unchanged.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        return query_contextual_cached(
            cache=redis_client,
            payload=payload.model_dump(),
            authorization=authorization,
            mapping_version=REDIS_MAPPING_WORKBOOK.stat().st_mtime_ns,
        )
    except ContextualLookupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        logger.exception("Contextual autocomplete cache request failed")
        raise HTTPException(status_code=503, detail="Autocomplete is temporarily unavailable.") from exc


@app.get("/period-options")
def period_options(
    report_name: str = Query(..., min_length=1),
    org_value: str = Query(..., min_length=1),
    period_type: str = Query(""),
    q: str = Query(""),
    limit: int = Query(40, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
) -> dict[str, Any]:
    """Return allowed PERIOD_TYPE values or PERIOD_NAME values scoped to org/type."""
    return _get_period_options(report_name, org_value, period_type, q, limit, offset)


@app.get("/autocomplete")
def autocomplete(
    report_name: str = Query(..., min_length=1),
    variable_name: str = Query(..., min_length=1),
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
    controller_variable_name: str = Query(""),
    controller_value: str = Query(""),
    period_type: str = Query(""),
) -> dict[str, Any]:
    """
    Return Redis autocomplete values.

    For a report configured with one controlling variable:

      - the controlling variable itself is searched globally;
      - every other Redis variable REQUIRES controller_value and is read only
        from that controller bucket.

    Example:
        Inventory Report controller = org_name
        buyer_name autocomplete + controller_value="ACD - SPARES"
        -> returns only buyers stored under ACD - SPARES.
    """

    mapping_report = resolve_mapping_report_for_any_name(report_name)

    if mapping_report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Report not found in mapping: {report_name}",
        )

    dependency = get_period_dependency(mapping_report["name"])
    requested_variable = make_identifier(variable_name)
    if dependency and requested_variable in {
        make_identifier(dependency["period_type_variable"]),
        make_identifier(dependency["period_name_variable"]),
    }:
        requested_controller = make_identifier(controller_variable_name)
        configured_controller = make_identifier(dependency["controller"])
        if requested_controller and requested_controller != configured_controller:
            raise HTTPException(400, detail="Wrong organization controller")
        if requested_variable == make_identifier(dependency["period_name_variable"]):
            if not str(period_type or "").strip():
                raise HTTPException(400, detail="Select period_type first")
            scoped_type = period_type
        else:
            scoped_type = ""
        return _get_period_options(
            mapping_report["name"], controller_value, scoped_type, q, limit, offset
        )

    mapping_variable = find_mapping_variable(mapping_report, variable_name)

    if mapping_variable is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Variable '{variable_name}' was not found under report "
                f"'{mapping_report['name']}'."
            ),
        )

    if not mapping_variable["is_redis_variable"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Variable '{mapping_variable['variable_name']}' is marked as "
                f"'{mapping_variable['storage_type']}'. Autocomplete is disabled "
                "for non-Redis variables."
            ),
        )

    report_id = mapping_report["id"]
    variable_id = make_identifier(mapping_variable["column_name"])

    redis_column_name = redis_client.hget(
        variable_names_key(report_id),
        variable_id,
    )

    if redis_column_name is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No Redis autocomplete values are loaded for report "
                f"'{mapping_report['name']}', column "
                f"'{mapping_variable['column_name']}'."
            ),
        )

    configured_controller = get_controller_variable(mapping_report["name"])
    configured_controller_id = make_identifier(configured_controller)
    target_logical_id = make_identifier(mapping_variable["variable_name"])
    target_is_controller = bool(
        configured_controller_id
        and target_logical_id == configured_controller_id
    )

    normalized_query = prefix_key(q)

    if configured_controller and not target_is_controller:
        requested_controller_id = make_identifier(controller_variable_name)

        if requested_controller_id and requested_controller_id != configured_controller_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This report is scoped by '{configured_controller}', not "
                    f"'{controller_variable_name}'."
                ),
            )

        cleaned_controller_value = str(controller_value or "").strip()
        if not cleaned_controller_value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Select a value for '{configured_controller}' before "
                    f"using autocomplete for '{mapping_variable['variable_name']}'."
                ),
            )

        if normalized_query:
            values = redis_client.smembers(
                scoped_variable_value_search_key(
                    report_id,
                    configured_controller_id,
                    cleaned_controller_value,
                    variable_id,
                    normalized_query,
                )
            )
        else:
            values = redis_client.smembers(
                scoped_variable_all_values_key(
                    report_id,
                    configured_controller_id,
                    cleaned_controller_value,
                    variable_id,
                )
            )
    else:
        if normalized_query:
            values = redis_client.smembers(
                variable_value_search_key(
                    report_id,
                    variable_id,
                    normalized_query,
                )
            )
        else:
            values = redis_client.smembers(
                variable_all_values_key(
                    report_id,
                    variable_id,
                )
            )

    sorted_values = sorted(values, key=str.casefold)
    page = slice_page(sorted_values, limit=limit, offset=offset)

    return {
        "requested_report_name": report_name,
        "report_name": mapping_report["name"],
        "variable_name": mapping_variable["variable_name"],
        "column_name": mapping_variable["column_name"],
        "table_name": mapping_variable["table_name"],
        "storage_type": mapping_variable["storage_type"],
        "query": q,
        "autocomplete_controller": configured_controller,
        "controller_value": (
            str(controller_value or "").strip()
            if configured_controller and not target_is_controller
            else None
        ),
        **page,
    }


@app.post("/reload")
def reload_data() -> dict[str, Any]:
    """
    Reload both workbooks and all JSON Redis values.
    """

    global redis_mapping_catalog
    global navigation_catalog

    redis_mapping_catalog = (
        load_redis_mapping_catalog()
    )

    navigation_catalog = (
        build_navigation_hierarchy()
    )

    redis_result = (
        load_snapshot()
    )

    return {
        "message": (
            "Workbooks and Redis data "
            "reloaded successfully"
        ),
        "mapping_report_count": (
            redis_mapping_catalog.get(
                "report_count",
                0,
            )
        ),
        "navigation_base_tab_count": (
            navigation_catalog.get(
                "base_tab_count",
                0,
            )
        ),
        "navigation_warning_count": (
            navigation_catalog.get(
                "warning_count",
                0,
            )
        ),
        "navigation_warnings": (
            navigation_catalog.get(
                "warnings",
                [],
            )
        ),
        "redis": (
            redis_result
        ),
    }


# =============================================================================
# Run server
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=6969,
        log_level="info",
    )