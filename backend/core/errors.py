from __future__ import annotations

import re
from typing import Any, Dict, Optional


_ORACLE_CODE_PATTERN = re.compile(r"\bORA-(\d{5})\b", re.IGNORECASE)


def _format_oracle_code(code: Any) -> Optional[str]:
    if code is None:
        return None

    try:
        return f"ORA-{int(code):05d}"
    except (TypeError, ValueError):
        text = str(code).strip().upper()

        match = _ORACLE_CODE_PATTERN.search(text)
        if match:
            return f"ORA-{match.group(1)}"

        return text or None


def extract_oracle_error_details(error: BaseException) -> Dict[str, Any]:
    """Extract stable Oracle error information from python-oracledb errors.

    ``oracledb.DatabaseError`` usually stores an Oracle error object in
    ``error.args[0]``. This helper also falls back to parsing ``str(error)`` so
    the workflow still retains an ORA code when the exception shape differs.
    """

    oracle_error = error.args[0] if getattr(error, "args", None) else None

    numeric_code = getattr(oracle_error, "code", None)
    message = getattr(oracle_error, "message", None)

    raw_message = str(message or error).strip()
    code = _format_oracle_code(numeric_code)

    if code is None:
        match = _ORACLE_CODE_PATTERN.search(raw_message)
        if match:
            code = f"ORA-{match.group(1)}"

    details: Dict[str, Any] = {}

    for attribute in (
        "offset",
        "context",
        "isrecoverable",
        "iswarning",
        "is_session_dead",
    ):
        value = getattr(oracle_error, attribute, None)
        if value is not None:
            details[attribute] = value

    return {
        "code": code,
        "message": raw_message or "Oracle database operation failed.",
        "details": details,
    }


class OracleQueryError(RuntimeError):
    """Application exception that preserves Oracle code and metadata."""

    def __init__(
        self,
        *,
        code: Optional[str],
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = code
        self.message = str(message or "Oracle database operation failed.")
        self.details = dict(details or {})

        rendered_message = (
            f"{self.code}: {self.message}"
            if self.code and self.code not in self.message
            else self.message
        )

        super().__init__(rendered_message)

    @classmethod
    def from_database_error(
        cls,
        error: BaseException,
    ) -> "OracleQueryError":
        extracted = extract_oracle_error_details(error)
        return cls(
            code=extracted["code"],
            message=extracted["message"],
            details=extracted["details"],
        )
