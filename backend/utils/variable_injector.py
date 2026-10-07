from __future__ import annotations

import re
from typing import Any, Dict, Iterable


PLACEHOLDER_PATTERN = re.compile(
    r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}"
)


def normalize_variables_key(
    variable_names: Iterable[str],
) -> str:
    """
    Convert variable names into the canonical key used by FAISS indexes.

    Examples:
        ["item", "buyer_name"] -> "buyer_name,item"
        [] -> "no_variables"
    """
    normalized_names = {
        str(name).strip().lower()
        for name in variable_names
        if str(name).strip()
    }

    if not normalized_names:
        return "no_variables"

    return ",".join(sorted(normalized_names))


def escape_sql_value(value: Any) -> str:
    """
    Escape single quotes before injecting a value into an SQL template.

    Example:
        O'Brien -> O''Brien
    """
    if value is None:
        raise ValueError(
            "Cannot inject a null value into SQL."
        )

    return str(value).replace("'", "''")


def get_template_variables(template: str) -> set[str]:
    """
    Return all variable placeholders found in a template.

    Example:
        "Buyer {buyer_name}, item {item}"
        -> {"buyer_name", "item"}
    """
    if not template:
        return set()

    return {
        variable_name.strip().lower()
        for variable_name in PLACEHOLDER_PATTERN.findall(
            template
        )
    }


def inject_variable_values(
    template: str,
    variable_values: Dict[str, Any],
    *,
    is_sql: bool = False,
) -> str:
    """
    Inject variable values into placeholders such as {buyer_name}.

    Raises ValueError if the template contains a placeholder for which
    no value was supplied.
    """
    if not template:
        return ""

    normalized_values = {
        str(name).strip().lower(): value
        for name, value in variable_values.items()
    }

    required_variables = get_template_variables(template)

    missing_variables = sorted(
        required_variables - normalized_values.keys()
    )

    if missing_variables:
        raise ValueError(
            "Missing values for template variables: "
            f"{missing_variables}"
        )

    def replacement(match: re.Match) -> str:
        variable_name = match.group(1).strip().lower()
        value = normalized_values[variable_name]

        if is_sql:
            return escape_sql_value(value)

        return str(value)

    return PLACEHOLDER_PATTERN.sub(
        replacement,
        template,
    )




def inject_repeated_variable_values(
    template: str,
    variable_values: Dict[str, Any],
    *,
    is_sql: bool = False,
) -> str:
    """
    Inject scalar or list values into repeated placeholders.

    Example:

        Template:
            BUYER_NAME LIKE '%{buyer_name}%'
            OR BUYER_NAME LIKE '%{buyer_name}%'

        Values:
            {
                "buyer_name": ["nitin", "shailesh"]
            }

        Result:
            BUYER_NAME LIKE '%nitin%'
            OR BUYER_NAME LIKE '%shailesh%'

    Scalar values continue to work normally.
    """

    if not template:
        return ""

    normalized_values = {
        str(name).strip().lower(): value
        for name, value in variable_values.items()
    }

    required_variables = get_template_variables(
        template
    )

    missing_variables = sorted(
        required_variables
        - normalized_values.keys()
    )

    if missing_variables:
        raise ValueError(
            "Missing values for template variables: "
            f"{missing_variables}"
        )

    occurrence_indexes: Dict[str, int] = {}

    def replacement(
        match: re.Match,
    ) -> str:
        variable_name = (
            match.group(1)
            .strip()
            .lower()
        )

        raw_value = normalized_values[
            variable_name
        ]

        if isinstance(
            raw_value,
            (list, tuple),
        ):
            values = [
                value
                for value in raw_value
                if value is not None
                and str(value).strip()
            ]

            if not values:
                raise ValueError(
                    f"No usable values were supplied for "
                    f"'{variable_name}'."
                )

            current_index = occurrence_indexes.get(
                variable_name,
                0,
            )

            if current_index >= len(values):
                value = values[-1]
            else:
                value = values[
                    current_index
                ]

            occurrence_indexes[
                variable_name
            ] = current_index + 1

        else:
            value = raw_value

        if is_sql:
            return escape_sql_value(
                value
            )

        return str(value)

    return PLACEHOLDER_PATTERN.sub(
        replacement,
        template,
    )