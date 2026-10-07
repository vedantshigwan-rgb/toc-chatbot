from __future__ import annotations

import re
from typing import Any, Dict

from core.models import SQLInjectionResult
from utils.variable_injector import (
    inject_repeated_variable_values,
)


PLACEHOLDER_PATTERN = re.compile(
    r"\{([A-Za-z_][A-Za-z0-9_]*)\}"
)


def clean_executable_sql(
    sql: str,
) -> str:
    """
    Remove line breaks and surrounding indentation without
    collapsing meaningful spaces inside SQL string values.

    Example:

        'SHAILESH  MADHUKAR'

    retains its intentional double space.
    """

    cleaned_sql = str(sql or "").strip()

    cleaned_sql = re.sub(
        r"[ \t]*[\r\n]+[ \t]*",
        " ",
        cleaned_sql,
    )

    return cleaned_sql.strip()


def find_unresolved_placeholders(
    sql: str,
) -> list[str]:
    """
    Return unique unresolved placeholder names in sorted order.
    """

    placeholders = PLACEHOLDER_PATTERN.findall(
        str(sql or "")
    )

    return sorted(set(placeholders))


def inject_sql_values(
    *,
    sql_template: str,
    variable_values: Dict[str, Any],
) -> SQLInjectionResult:
    """
    Inject request variable values into a generated SQL template.

    This service does not execute SQL. It only:

    1. Injects scalar and repeated values.
    2. Cleans SQL line breaks.
    3. Detects unresolved placeholders.
    4. Returns a structured result.
    """

    normalized_template = str(
        sql_template or ""
    ).strip()

    if not normalized_template:
        return SQLInjectionResult(
            success=False,
            sql_template="",
            executable_sql=None,
            message="The SQL template is empty.",
            exception_type=None,
        )

    try:
        executable_sql = (
            inject_repeated_variable_values(
                template=normalized_template,
                variable_values=variable_values,
                is_sql=True,
            )
        )

        executable_sql = clean_executable_sql(
            executable_sql
        )

    except Exception as error:
        return SQLInjectionResult(
            success=False,
            sql_template=normalized_template,
            executable_sql=None,
            message=str(error),
            exception_type=type(error).__name__,
        )

    unresolved_placeholders = (
        find_unresolved_placeholders(
            executable_sql
        )
    )

    if unresolved_placeholders:
        return SQLInjectionResult(
            success=False,
            sql_template=normalized_template,
            executable_sql=executable_sql,
            unresolved_placeholders=(
                unresolved_placeholders
            ),
            message=(
                "The final SQL contains unresolved "
                "placeholders: "
                f"{unresolved_placeholders}"
            ),
        )

    return SQLInjectionResult(
        success=True,
        sql_template=normalized_template,
        executable_sql=executable_sql,
        unresolved_placeholders=[],
        message=None,
        exception_type=None,
    )