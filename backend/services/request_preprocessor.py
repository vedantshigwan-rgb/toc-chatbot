from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agents.question_normalizer import normalize_question
from core.models import PreparedRequest
from utils.variable_injector import normalize_variables_key


def normalize_request_variables(
    variables: Optional[List[Dict[str, Any]]],
) -> tuple[
    List[Dict[str, Any]],
    List[str],
    Dict[str, Any],
]:
    """
    Normalize variable names and group repeated values.

    Internal spaces inside variable values are preserved.
    """

    normalized_variables: List[Dict[str, Any]] = []
    variable_names: List[str] = []
    variable_values: Dict[str, Any] = {}

    for variable in variables or []:
        if not isinstance(variable, dict):
            continue

        variable_name = str(
            variable.get("name", "")
        ).strip().lower()

        variable_value = variable.get("value")

        if not variable_name:
            continue

        if variable_value is None:
            continue

        if isinstance(variable_value, str):
            variable_value = variable_value.strip()

            if not variable_value:
                continue

        normalized_variables.append(
            {
                "name": variable_name,
                "value": variable_value,
            }
        )

        if variable_name not in variable_names:
            variable_names.append(variable_name)

        if variable_name not in variable_values:
            variable_values[variable_name] = variable_value
            continue

        existing_value = variable_values[variable_name]

        if not isinstance(existing_value, list):
            existing_value = [existing_value]

        existing_value.append(variable_value)

        variable_values[variable_name] = existing_value

    return (
        normalized_variables,
        variable_names,
        variable_values,
    )


def _prepare_variable_state(
    variables: Optional[List[Dict[str, Any]]],
) -> tuple[
    List[Dict[str, Any]],
    List[str],
    Dict[str, Any],
    List[Dict[str, Any]],
    List[str],
    Dict[str, Any],
]:
    """Prepare source=input and source=option variable state."""

    input_variables_raw: List[Dict[str, Any]] = []
    option_variables_raw: List[Dict[str, Any]] = []

    for variable in variables or []:
        if not isinstance(variable, dict):
            continue

        source = str(
            variable.get("source", "input")
            or "input"
        ).strip().lower()

        if source == "option":
            option_variables_raw.append(variable)
        else:
            input_variables_raw.append(variable)

    (
        normalized_input_variables,
        variable_names,
        variable_values,
    ) = normalize_request_variables(input_variables_raw)

    normalized_input_variables = [
        {
            **variable,
            "source": "input",
        }
        for variable in normalized_input_variables
    ]

    (
        normalized_option_variables,
        extra_filter_names,
        extra_filter_values,
    ) = normalize_request_variables(option_variables_raw)

    normalized_option_variables = [
        {
            **variable,
            "source": "option",
        }
        for variable in normalized_option_variables
    ]

    return (
        normalized_input_variables,
        variable_names,
        variable_values,
        normalized_option_variables,
        extra_filter_names,
        extra_filter_values,
    )


def _clean_normalized_question(value: Any) -> str:
    """Apply only safe whitespace/trailing-conjunction cleanup."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"\s+\b(and|or)\b\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return text


def _build_prepared_request(
    *,
    user_input: str,
    report_name: str,
    normalized_input: str,
    variables: Optional[List[Dict[str, Any]]],
) -> PreparedRequest:
    (
        normalized_input_variables,
        variable_names,
        variable_values,
        normalized_option_variables,
        extra_filter_names,
        extra_filter_values,
    ) = _prepare_variable_state(variables)

    normalized_variables = (
        normalized_input_variables
        + normalized_option_variables
    )

    query_without_values = _clean_normalized_question(
        normalized_input
    )

    variables_key = normalize_variables_key(
        variable_names
    )

    return PreparedRequest(
        user_input=str(user_input or "").strip(),
        report_name=str(report_name or "").strip(),
        normalized_variables=normalized_variables,
        variable_names=variable_names,
        variable_values=variable_values,
        extra_filter_variables=normalized_option_variables,
        extra_filter_names=extra_filter_names,
        extra_filter_values=extra_filter_values,
        normalized_input=query_without_values,
        query_without_values=query_without_values,
        variables_key=variables_key,
    )


def prepare_request(
    *,
    user_input: str,
    report_name: str,
    variables: Optional[List[Dict[str, Any]]],
) -> PreparedRequest:
    """
    Prepare a normal frontend request for FAQ retrieval and SQL generation.

    source=input variables participate in FAISS and base SQL generation.
    source=option variables are applied later as deterministic SQL filters.
    """

    (
        normalized_input_variables,
        _variable_names,
        _variable_values,
        _normalized_option_variables,
        _extra_filter_names,
        _extra_filter_values,
    ) = _prepare_variable_state(variables)

    query_without_values = normalize_question(
        user_input,
        normalized_input_variables,
        require_all_input_variables=True,
    )

    return _build_prepared_request(
        user_input=user_input,
        report_name=report_name,
        normalized_input=query_without_values,
        variables=variables,
    )


def prepare_normalized_request(
    *,
    normalized_input: str,
    report_name: str,
    variables: Optional[List[Dict[str, Any]]],
) -> PreparedRequest:
    """
    Prepare an already-normalized semantic request.

    This is the feedback entry point. The caller already owns the normalized
    business question, so we intentionally do NOT rebuild /name {value}
    sentence syntax and we do NOT run question normalization again.

    Structured variables remain authoritative for:
      - FAISS variable-index selection
      - SQL placeholders / values
      - sidebar option filters
    """

    cleaned_question = _clean_normalized_question(
        normalized_input
    )

    return _build_prepared_request(
        user_input=cleaned_question,
        report_name=report_name,
        normalized_input=cleaned_question,
        variables=variables,
    )
