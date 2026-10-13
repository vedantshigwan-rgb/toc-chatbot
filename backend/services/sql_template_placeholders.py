"""Protect {variable} placeholders while SQLGlot parses and rewrites SQL.

SQLGlot understands Oracle SQL, not this application's template syntax:
    QUANTITY > {quantity}
Without masking, serialization can turn the placeholder into STRUCT(quantity).

This module does not bind values or change predicates. It only provides
SQL-parseable, unique identifier markers and restores the original spelling.
"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import uuid4

# Only application variable placeholders, not general JSON / arbitrary braces.
_VARIABLE_PLACEHOLDER = re.compile(
    r"(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})"
)


class SQLTemplatePlaceholderGuard:
    """Mask placeholders before an SQL AST round-trip, then restore them.

    Each occurrence has its own token, so case and whitespace are retained.
    SQLGlot may normalize identifier case; restoration is case-insensitive.
    """

    def __init__(self, sql: str) -> None:
        self._original_by_token: dict[str, str] = {}
        self._tokens_by_variable: dict[str, set[str]] = defaultdict(set)
        nonce = uuid4().hex.upper()

        def mask(match: re.Match[str]) -> str:
            token = f"TOCPH{nonce}X{len(self._original_by_token)}XEND"
            self._original_by_token[token] = match.group(0)
            self._tokens_by_variable[match.group(1).casefold()].add(token)
            return token

        self.protected_sql = _VARIABLE_PLACEHOLDER.sub(mask, str(sql))

    def contains_variable(self, expression_sql: str, variable_name: str) -> bool:
        """Recognize masked as well as newly generated literal placeholders."""
        name = str(variable_name or "").strip().casefold()
        text = str(expression_sql or "")

        if any(token.casefold() in text.casefold()
               for token in self._tokens_by_variable.get(name, ())):
            return True

        return any(
            match.group(1).casefold() == name
            for match in _VARIABLE_PLACEHOLDER.finditer(text)
        )

    def restore(self, serialized_sql: str) -> str:
        """Put back original {name} text after SQLGlot serialization."""
        result = str(serialized_sql or "")
        for token, original in self._original_by_token.items():
            # sqlglot sometimes capitalizes identifiers when using Oracle.
            result = re.sub(
                re.escape(token),
                lambda _match, literal=original: literal,
                result,
                flags=re.IGNORECASE,
            )

        # Defensive invariant: never send internal markers to SQL injection.
        for token in self._original_by_token:
            if token.casefold() in result.casefold():
                raise ValueError("SQL placeholder restoration is incomplete")

        return result
