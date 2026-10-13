from __future__ import annotations

import re
from typing import Optional


# -----------------------------------------------------------------------------
# ONE controlling autocomplete variable per report.
#
# Add/edit entries here as you decide the controlling option for each report.
# The value is the LOGICAL variable name from "redis mapping.xlsx" and
# "Roles and options.xlsx", not the Oracle column name.
# -----------------------------------------------------------------------------
REPORT_AUTOCOMPLETE_CONTROLLERS: dict[str, str] = {
    "Inventory Report": "org_name",
    "Buyer Wise Inventory Report": "org_name",

    # Examples - enable only when you want these reports scoped the same way:
    "OA Trolley Report": "org_name",
    "Buyerwise OA Trolley Report": "org_name",
    "Order Report": "org_name",
    "BOM Report": "org_name",
    "On Time Delivery Index Report": "business_group",
    "PO Board Report": "org_name",
    "Docket Report": "org_name",
    "Docket Employee Report": "employee_org",
}


_non_alphanumeric_pattern = re.compile(r"[^a-z0-9]+")
_multiple_spaces_pattern = re.compile(r"\s+")


def _normalize(value: object) -> str:
    text = "" if value is None else str(value).strip().lower()
    text = _non_alphanumeric_pattern.sub(" ", text)
    text = _multiple_spaces_pattern.sub(" ", text)
    return text.strip()


def get_controller_variable(report_name: object) -> Optional[str]:
    """Return the configured logical controller variable for a report."""

    requested = _normalize(report_name)
    if not requested:
        return None

    for configured_report, variable_name in REPORT_AUTOCOMPLETE_CONTROLLERS.items():
        if _normalize(configured_report) == requested:
            cleaned = str(variable_name or "").strip()
            return cleaned or None

    return None


# Period dropdowns are separate from the standard single-controller autocomplete.
# The Docket Report uses virtual PERIOD_TYPE/PERIOD_NAME values built from
# YEAR_PERIOD, MONTH_PERIOD, and WEEK_PERIOD (not Oracle columns named PERIOD_*).
REPORT_PERIOD_DEPENDENCIES: dict[str, dict] = {
    "Docket Report": {
        "controller": "org_name",
        "period_type_variable": "period_type",
        "period_name_variable": "period_name",
        "virtual_period_columns": {
            "Year": "year_period",
            "Month": "month_period",
            "Week": "week_period",
        },
    },
    "Docket Employee Report": {
        "controller": "employee_org",
        "period_type_variable": "period_type",
        "period_name_variable": "period_name",
        "virtual_period_columns": {},
    },
}


def get_period_dependency(report_name: object) -> Optional[dict]:
    requested = _normalize(report_name)
    for configured_report, settings in REPORT_PERIOD_DEPENDENCIES.items():
        if _normalize(configured_report) == requested:
            return dict(settings)
    return None
