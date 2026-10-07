from __future__ import annotations

import re
from typing import Any, Iterable, Optional


VARIABLE_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
VARIABLE_PAIR_PATTERN = re.compile(
    rf"/(?P<name>{VARIABLE_NAME_PATTERN})\s*\{{(?P<value>[^{{}}]*)\}}"
)
SLASH_VARIABLE_PATTERN = re.compile(
    rf"/(?P<name>{VARIABLE_NAME_PATTERN})"
)
BRACE_VALUE_PATTERN = re.compile(r"\{(?P<value>[^{}]*)\}")


class QuestionNormalizationError(ValueError):
    """Raised when sentence variable syntax disagrees with structured variables."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def normalize_variable_name(variable_name: Any) -> str:
    """Convert a canonical variable name into readable text."""
    if variable_name is None:
        return ""

    text = str(variable_name).strip()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def remove_repeated_adjacent_phrase(text: Any) -> str:
    """Remove immediately repeated words or phrases."""
    if text is None:
        return ""

    words = str(text).split()
    if not words:
        return ""

    changed = True
    while changed:
        changed = False
        max_phrase_length = len(words) // 2

        for phrase_length in range(max_phrase_length, 0, -1):
            index = 0
            while index + (2 * phrase_length) <= len(words):
                first_phrase = words[index:index + phrase_length]
                second_phrase = words[
                    index + phrase_length:index + (2 * phrase_length)
                ]

                if [w.lower() for w in first_phrase] == [
                    w.lower() for w in second_phrase
                ]:
                    del words[
                        index + phrase_length:index + (2 * phrase_length)
                    ]
                    changed = True
                else:
                    index += 1

    return " ".join(words)


def _canonical_input_variables(
    variables: Optional[Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return canonical source=input variables while preserving duplicates/order."""
    canonical: list[dict[str, Any]] = []

    for variable in variables or []:
        if not isinstance(variable, dict):
            continue

        source = str(variable.get("source") or "input").strip().lower()
        if source == "option":
            continue

        name = str(variable.get("name") or "").strip().lower()
        if not name:
            continue

        value = variable.get("value")
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()

        canonical.append(
            {
                "name": name,
                "value": value,
                "value_text": str(value),
                "source": "input",
            }
        )

    return canonical


def normalize_question(
    question: Any,
    variables: Optional[Iterable[dict[str, Any]]] = None,
    *,
    require_all_input_variables: bool = True,
) -> str:
    """
    Normalize a request using the structured variable payload as the authority.

    Contract for every source=input variable occurrence in the sentence:

        /<exact_variable_name> {<exact_variable_value>}

    Only verified name/value pairs are normalized. No arbitrary slash token or
    arbitrary brace content is removed.

    Example:
        question:
            Show excess stock for /buyer_name {Nanaware, Mr. Nitin S}
        variables:
            [{"name": "buyer_name", "value": "Nanaware, Mr. Nitin S",
              "source": "input"}]
        output:
            Show excess stock for buyer name
    """
    if question is None:
        return ""

    text = str(question).strip()
    if not text:
        return ""

    canonical_variables = _canonical_input_variables(variables)

    # When there are no structured input variables, do not treat arbitrary
    # slash text (for example 1-1/2 or a path) as variable syntax.
    if not canonical_variables:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"\s+([,.?!;:])", r"\1", cleaned)
        cleaned = re.sub(r"[,.?!;:]+$", "", cleaned).strip()
        return cleaned

    available_by_name: dict[str, list[dict[str, Any]]] = {}
    for variable in canonical_variables:
        available_by_name.setdefault(variable["name"], []).append(variable)

    consumed_indexes: set[int] = set()
    replacements: list[tuple[int, int, str]] = []
    matched_pair_spans: list[tuple[int, int]] = []

    for match in VARIABLE_PAIR_PATTERN.finditer(text):
        name = match.group("name").strip().lower()
        sentence_value = match.group("value").strip()
        candidates = available_by_name.get(name, [])

        if not candidates:
            raise QuestionNormalizationError(
                f"Variable '/{name}' is not present in the structured input variables.",
                code="unknown_sentence_variable",
                details={"variable_name": name},
            )

        matched_variable_index: Optional[int] = None
        for index, variable in enumerate(canonical_variables):
            if index in consumed_indexes:
                continue
            if variable["name"] != name:
                continue
            if variable["value_text"] == sentence_value:
                matched_variable_index = index
                break

        if matched_variable_index is None:
            expected_values = [item["value_text"] for item in candidates]
            raise QuestionNormalizationError(
                (
                    f"The value inside '/{name} {{...}}' does not match the "
                    "structured variable value."
                ),
                code="variable_value_mismatch",
                details={
                    "variable_name": name,
                    "sentence_value": sentence_value,
                    "payload_values": expected_values,
                },
            )

        consumed_indexes.add(matched_variable_index)
        matched_pair_spans.append(match.span())
        replacements.append(
            (
                match.start(),
                match.end(),
                normalize_variable_name(name),
            )
        )

    def _inside_verified_pair(start: int, end: int) -> bool:
        return any(
            pair_start <= start and end <= pair_end
            for pair_start, pair_end in matched_pair_spans
        )

    # A known payload variable written with a slash but not as /name {value}
    # violates the request contract. Unknown slash text is left alone unless it
    # formed a complete variable pair above, which prevents fractions such as
    # 1-1/2 from being misclassified.
    known_names = set(available_by_name)
    for slash_match in SLASH_VARIABLE_PATTERN.finditer(text):
        slash_name = slash_match.group("name").strip().lower()
        if slash_name not in known_names:
            continue
        if _inside_verified_pair(*slash_match.span()):
            continue
        raise QuestionNormalizationError(
            (
                f"Variable '/{slash_name}' must use the exact syntax "
                f"'/{slash_name} {{value}}'."
            ),
            code="invalid_variable_syntax",
            details={"variable_name": slash_name},
        )

    # If a payload value appears in braces but was not attached to the matching
    # /name token, reject it instead of silently deleting it.
    payload_value_texts = {item["value_text"] for item in canonical_variables}
    for brace_match in BRACE_VALUE_PATTERN.finditer(text):
        if _inside_verified_pair(*brace_match.span()):
            continue
        brace_value = brace_match.group("value").strip()
        if brace_value in payload_value_texts:
            raise QuestionNormalizationError(
                "A structured variable value was found in braces without its /variable_name token.",
                code="orphan_variable_value",
                details={"sentence_value": brace_value},
            )

    if require_all_input_variables:
        missing = [
            canonical_variables[index]["name"]
            for index in range(len(canonical_variables))
            if index not in consumed_indexes
        ]
        if missing:
            raise QuestionNormalizationError(
                "Every source=input variable must appear in the sentence as /name {value}.",
                code="input_variable_missing_from_sentence",
                details={"missing_variable_names": missing},
            )

    # Rebuild from right to left so source spans remain stable.
    normalized = text
    for start, end, replacement in reversed(replacements):
        normalized = normalized[:start] + replacement + normalized[end:]

    normalized = remove_repeated_adjacent_phrase(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+([,.?!;:])", r"\1", normalized)
    normalized = re.sub(r"[,.?!;:]+$", "", normalized).strip()

    return normalized


def remove_repeated_variable_mentions(
    text: str,
    variable_names: Iterable[str],
) -> str:
    """
    Backward-compatible helper retained for callers outside the request path.

    New request normalization does not call this function because repeated
    variable occurrences may represent multiple values and must not be erased.
    """
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        return ""

    unique_variable_names = {
        normalize_variable_name(variable_name)
        for variable_name in variable_names or []
        if normalize_variable_name(variable_name)
    }

    for readable_name in unique_variable_names:
        pattern = re.compile(
            rf"\b{re.escape(readable_name)}\b",
            flags=re.IGNORECASE,
        )
        occurrence_count = 0

        def replace_repeated(match: re.Match) -> str:
            nonlocal occurrence_count
            occurrence_count += 1
            return match.group(0) if occurrence_count == 1 else ""

        cleaned_text = pattern.sub(replace_repeated, cleaned_text)

    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    cleaned_text = re.sub(
        r"\b(and|or)\s+(?=for\b)",
        "",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    cleaned_text = re.sub(
        r"\b(and|or)\s+(and|or)\b",
        r"\1",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned_text).strip()
