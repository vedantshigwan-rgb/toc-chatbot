from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.enums import (
    OracleErrorCategory,
    RecoveryAction,
)
from core.models import (
    OracleErrorClassification,
)


ORACLE_CODE_PATTERN = re.compile(
    r"\bORA-(\d{5})\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class OracleErrorRule:
    category: OracleErrorCategory
    retryable: bool
    recommended_action: RecoveryAction
    summary: str


ORACLE_ERROR_RULES: Dict[
    str,
    OracleErrorRule,
] = {
    # ========================================================
    # Metadata and identifier problems
    # ========================================================

    "ORA-00904": OracleErrorRule(
        category=(
            OracleErrorCategory.INVALID_COLUMN
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.VALIDATE_METADATA
        ),
        summary=(
            "The SQL references an invalid column "
            "or identifier."
        ),
    ),

    "ORA-00942": OracleErrorRule(
        category=(
            OracleErrorCategory.INVALID_TABLE_OR_VIEW
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.VALIDATE_METADATA
        ),
        summary=(
            "The referenced table or view does not "
            "exist or is not accessible."
        ),
    ),

    # ========================================================
    # SQL syntax problems
    # ========================================================

    "ORA-00900": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary="The SQL statement is invalid.",
    ),

    "ORA-00905": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL statement is missing a keyword."
        ),
    ),

    "ORA-00907": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL statement has a missing "
            "right parenthesis."
        ),
    ),

    "ORA-00920": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL statement contains an invalid "
            "relational operator."
        ),
    ),

    "ORA-00923": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL statement has an invalid or "
            "missing FROM clause."
        ),
    ),

    "ORA-00933": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL command is not properly ended."
        ),
    ),

    "ORA-00936": OracleErrorRule(
        category=(
            OracleErrorCategory.SQL_SYNTAX_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL statement is missing an "
            "expression."
        ),
    ),

    "ORA-00979": OracleErrorRule(
        category=(
            OracleErrorCategory.INVALID_GROUPING
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL SELECT and GROUP BY clauses "
            "are inconsistent."
        ),
    ),

    # ========================================================
    # Datatype, date and bind problems
    # ========================================================

    "ORA-01722": OracleErrorRule(
        category=(
            OracleErrorCategory.DATATYPE_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "A value could not be converted to a "
            "valid number."
        ),
    ),

    "ORA-06502": OracleErrorRule(
        category=(
            OracleErrorCategory.DATATYPE_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "Oracle encountered a numeric or value "
            "conversion error."
        ),
    ),

    "ORA-01830": OracleErrorRule(
        category=(
            OracleErrorCategory.DATE_FORMAT_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The date format does not match the "
            "complete input value."
        ),
    ),

    "ORA-01843": OracleErrorRule(
        category=(
            OracleErrorCategory.DATE_FORMAT_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary="The SQL contains an invalid month.",
    ),

    "ORA-01858": OracleErrorRule(
        category=(
            OracleErrorCategory.DATE_FORMAT_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "A non-numeric value appears where a "
            "numeric date value was expected."
        ),
    ),

    "ORA-01861": OracleErrorRule(
        category=(
            OracleErrorCategory.DATE_FORMAT_ERROR
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "A date literal does not match its "
            "format string."
        ),
    ),

    "ORA-01008": OracleErrorRule(
        category=OracleErrorCategory.BIND_ERROR,
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL contains bind variables that "
            "were not supplied."
        ),
    ),

    "ORA-01036": OracleErrorRule(
        category=OracleErrorCategory.BIND_ERROR,
        retryable=True,
        recommended_action=(
            RecoveryAction.REPAIR_SQL
        ),
        summary=(
            "The SQL contains an invalid bind "
            "variable name or number."
        ),
    ),

    # ========================================================
    # Authentication and permission problems
    # ========================================================

    "ORA-01017": OracleErrorRule(
        category=(
            OracleErrorCategory.AUTHENTICATION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "Oracle rejected the configured username "
            "or password."
        ),
    ),

    "ORA-01031": OracleErrorRule(
        category=(
            OracleErrorCategory.PERMISSION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "The database user does not have the "
            "required privileges."
        ),
    ),

    "ORA-28000": OracleErrorRule(
        category=(
            OracleErrorCategory.AUTHENTICATION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "The configured Oracle account is locked."
        ),
    ),

    "ORA-28001": OracleErrorRule(
        category=(
            OracleErrorCategory.AUTHENTICATION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "The configured Oracle password has "
            "expired."
        ),
    ),

    # ========================================================
    # Connection configuration problems
    # ========================================================

    "ORA-12154": OracleErrorRule(
        category=(
            OracleErrorCategory
            .CONNECTION_CONFIGURATION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "Oracle could not resolve the configured "
            "connection identifier."
        ),
    ),

    "ORA-02019": OracleErrorRule(
        category=(
            OracleErrorCategory
            .CONNECTION_CONFIGURATION_ERROR
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "The connection description for a remote "
            "database could not be found."
        ),
    ),

    # ========================================================
    # Transient connection problems
    # ========================================================

    "ORA-12514": OracleErrorRule(
        category=(
            OracleErrorCategory.CONNECTION_UNAVAILABLE
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The Oracle listener does not currently "
            "know the requested service."
        ),
    ),

    "ORA-12541": OracleErrorRule(
        category=(
            OracleErrorCategory.CONNECTION_UNAVAILABLE
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The Oracle listener is unavailable."
        ),
    ),

    "ORA-12545": OracleErrorRule(
        category=(
            OracleErrorCategory.CONNECTION_UNAVAILABLE
        ),
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "Oracle could not connect because the "
            "target host or object does not exist."
        ),
    ),

    "ORA-12170": OracleErrorRule(
        category=OracleErrorCategory.TIMEOUT,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The Oracle connection attempt timed out."
        ),
    ),

    "ORA-03113": OracleErrorRule(
        category=OracleErrorCategory.SESSION_LOST,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The database connection was unexpectedly "
            "terminated."
        ),
    ),

    "ORA-03114": OracleErrorRule(
        category=OracleErrorCategory.SESSION_LOST,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The application is no longer connected "
            "to Oracle."
        ),
    ),

    "ORA-01012": OracleErrorRule(
        category=OracleErrorCategory.SESSION_LOST,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_CONNECTION
        ),
        summary=(
            "The current Oracle session is no longer "
            "logged on."
        ),
    ),

    # ========================================================
    # Resource contention
    # ========================================================

    "ORA-00054": OracleErrorRule(
        category=OracleErrorCategory.RESOURCE_BUSY,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_EXECUTION
        ),
        summary=(
            "The requested database resource is "
            "currently busy."
        ),
    ),

    "ORA-00060": OracleErrorRule(
        category=OracleErrorCategory.RESOURCE_BUSY,
        retryable=True,
        recommended_action=(
            RecoveryAction.RETRY_EXECUTION
        ),
        summary=(
            "Oracle detected a deadlock while "
            "executing the operation."
        ),
    ),

    # ========================================================
    # Cancellation and internal database failures
    # ========================================================

    "ORA-01013": OracleErrorRule(
        category=(
            OracleErrorCategory.QUERY_CANCELLED
        ),
        retryable=False,
        recommended_action=RecoveryAction.STOP,
        summary=(
            "The Oracle operation was cancelled."
        ),
    ),

    "ORA-00600": OracleErrorRule(
        category=(
            OracleErrorCategory.DATABASE_INTERNAL_ERROR
        ),
        retryable=False,
        recommended_action=(
            RecoveryAction.INVESTIGATE
        ),
        summary=(
            "Oracle reported an internal database "
            "error."
        ),
    ),

    "ORA-07445": OracleErrorRule(
        category=(
            OracleErrorCategory.DATABASE_INTERNAL_ERROR
        ),
        retryable=False,
        recommended_action=(
            RecoveryAction.INVESTIGATE
        ),
        summary=(
            "Oracle encountered an internal exception."
        ),
    ),
}


MESSAGE_FALLBACK_RULES = [
    (
        "invalid identifier",
        OracleErrorRule(
            category=(
                OracleErrorCategory.INVALID_COLUMN
            ),
            retryable=True,
            recommended_action=(
                RecoveryAction.VALIDATE_METADATA
            ),
            summary=(
                "The SQL references an invalid column "
                "or identifier."
            ),
        ),
    ),
    (
        "table or view does not exist",
        OracleErrorRule(
            category=(
                OracleErrorCategory
                .INVALID_TABLE_OR_VIEW
            ),
            retryable=True,
            recommended_action=(
                RecoveryAction.VALIDATE_METADATA
            ),
            summary=(
                "The referenced table or view does not "
                "exist or is not accessible."
            ),
        ),
    ),
    (
        "not all variables bound",
        OracleErrorRule(
            category=(
                OracleErrorCategory.BIND_ERROR
            ),
            retryable=True,
            recommended_action=(
                RecoveryAction.REPAIR_SQL
            ),
            summary=(
                "The SQL contains bind variables that "
                "were not supplied."
            ),
        ),
    ),
    (
        "invalid username/password",
        OracleErrorRule(
            category=(
                OracleErrorCategory
                .AUTHENTICATION_ERROR
            ),
            retryable=False,
            recommended_action=(
                RecoveryAction.STOP
            ),
            summary=(
                "Oracle rejected the configured "
                "username or password."
            ),
        ),
    ),
    (
        "insufficient privileges",
        OracleErrorRule(
            category=(
                OracleErrorCategory.PERMISSION_ERROR
            ),
            retryable=False,
            recommended_action=(
                RecoveryAction.STOP
            ),
            summary=(
                "The database user does not have the "
                "required privileges."
            ),
        ),
    ),
    (
        "no listener",
        OracleErrorRule(
            category=(
                OracleErrorCategory
                .CONNECTION_UNAVAILABLE
            ),
            retryable=True,
            recommended_action=(
                RecoveryAction.RETRY_CONNECTION
            ),
            summary=(
                "The Oracle listener is unavailable."
            ),
        ),
    ),
    (
        "not connected to oracle",
        OracleErrorRule(
            category=(
                OracleErrorCategory.SESSION_LOST
            ),
            retryable=True,
            recommended_action=(
                RecoveryAction.RETRY_CONNECTION
            ),
            summary=(
                "The application is no longer "
                "connected to Oracle."
            ),
        ),
    ),
    (
        "timeout",
        OracleErrorRule(
            category=OracleErrorCategory.TIMEOUT,
            retryable=True,
            recommended_action=(
                RecoveryAction.RETRY_CONNECTION
            ),
            summary=(
                "The Oracle operation timed out."
            ),
        ),
    ),
]


def normalize_oracle_code(
    code: Optional[str],
    message: Optional[str] = None,
) -> Optional[str]:
    """
    Return an Oracle error code in canonical ORA-00000 form.

    The function first checks the explicit code and then searches
    the message.
    """

    for candidate in (
        code,
        message,
    ):
        if not candidate:
            continue

        match = ORACLE_CODE_PATTERN.search(
            str(candidate)
        )

        if match:
            return f"ORA-{match.group(1)}"

    return None


def classify_oracle_error(
    *,
    code: Optional[str],
    message: Optional[str],
    details: Optional[Dict[str, Any]] = None,
) -> OracleErrorClassification:
    """
    Classify an Oracle error without using an LLM.

    Code-based rules take priority. Message-based matching is used
    only when an Oracle code is unavailable or unknown.
    """

    normalized_code = normalize_oracle_code(
        code=code,
        message=message,
    )

    normalized_message = str(
        message or ""
    ).strip()

    if normalized_code in ORACLE_ERROR_RULES:
        rule = ORACLE_ERROR_RULES[
            normalized_code
        ]

        return OracleErrorClassification(
            code=normalized_code,
            category=rule.category,
            retryable=rule.retryable,
            recommended_action=(
                rule.recommended_action
            ),
            summary=rule.summary,
            matched_by="code",
            original_message=(
                normalized_message or None
            ),
            details=dict(details or {}),
        )

    lowercase_message = (
        normalized_message.lower()
    )

    for phrase, rule in MESSAGE_FALLBACK_RULES:
        if phrase in lowercase_message:
            return OracleErrorClassification(
                code=normalized_code,
                category=rule.category,
                retryable=rule.retryable,
                recommended_action=(
                    rule.recommended_action
                ),
                summary=rule.summary,
                matched_by="message",
                original_message=(
                    normalized_message or None
                ),
                details=dict(details or {}),
            )

    return OracleErrorClassification(
        code=normalized_code,
        category=(
            OracleErrorCategory
            .UNKNOWN_DATABASE_ERROR
        ),
        retryable=False,
        recommended_action=(
            RecoveryAction.INVESTIGATE
        ),
        summary=(
            "The Oracle error is not currently covered "
            "by a deterministic classification rule."
        ),
        matched_by="default",
        original_message=(
            normalized_message or None
        ),
        details=dict(details or {}),
    )