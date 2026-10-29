from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from utils.openai_pool import OpenAIPool
import asyncio


def clean_prompt_text(value: Any) -> str:
    """
    Convert a value into a clean string while preserving SQL line breaks
    where possible.
    """

    if value is None:
        return ""

    return str(value).strip()


def prepare_few_shot_examples(
    examples: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Prepare one or more FAISS examples for the LLM.

    Accepted example structure:

        {
            "display_question_template": "...",
            "sql_template": "..."
        }

    It also accepts:

        {
            "display_question": "...",
            "sql": "..."
        }
    """

    prepared_examples: List[Dict[str, str]] = []

    for example in examples or []:
        if not isinstance(example, dict):
            continue

        display_question = clean_prompt_text(
            example.get("display_question_template")
            or example.get("display_question")
            or example.get("matched_question")
        )

        sql_template = clean_prompt_text(
            example.get("sql_template")
            or example.get("sql")
        )

        if not display_question:
            continue

        if not sql_template:
            continue

        prepared_examples.append(
            {
                "display_question": display_question,
                "sql_template": sql_template,
            }
        )

    return prepared_examples


def build_few_shot_examples_text(
    examples: List[Dict[str, str]],
) -> str:
    """
    Convert prepared examples into ranked prompt text.
    """

    example_blocks: List[str] = []

    for index, example in enumerate(
        examples,
        start=1,
    ):
        example_blocks.append(
            f"""
EXAMPLE {index}

Display question:
{example["display_question"]}

Associated SQL template:
{example["sql_template"]}
""".strip()
        )

    return "\n\n".join(example_blocks)


async def fetch_weaviate_metadata(
    metadata_repository,
    allowed_tables: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch column metadata only for tables trusted by the FAQ examples.
    """

    if metadata_repository is None or not allowed_tables:
        return []

    table_names = sorted(
        {
            str(table).strip().split(".")[-1]
            for table in allowed_tables
            if table and str(table).strip()
        }
    )

    metadata_records: List[Dict[str, Any]] = []
    seen_records = set()

    for table_name in table_names:
        try:
            lookup = (
                await metadata_repository.get_table_columns_any_module(
                    table_name=table_name,
                    limit=500,
                )
            )
        except Exception:
            # Metadata failure should not prevent existing SQL generation.
            continue

        if not getattr(lookup, "success", False):
            continue

        if not getattr(lookup, "found", False):
            continue

        for record in (getattr(lookup, "records", None) or []):
            if isinstance(record, dict):
                record_data = record
            elif hasattr(record, "model_dump"):
                record_data = record.model_dump()
            elif hasattr(record, "dict"):
                record_data = record.dict()
            else:
                continue

            
            item = {
                "columnName": (
                    record_data.get("columnName")
                    or record_data.get("column_name")
                ),
                "TableName": (
                    record_data.get("TableName")
                    or record_data.get("table_name")
                ),
                "dataType": (
                    record_data.get("dataType")
                    or record_data.get("data_type")
                ),
                "description": record_data.get("description"),
            }

            item = {
                key: value
                for key, value in item.items()
                if value is not None
            }

            identity = (
                str(item.get("TableName", "")).upper(),
                str(item.get("columnName", "")).upper(),
            )

            if identity in seen_records:
                continue

            seen_records.add(identity)
            metadata_records.append(item)

    return metadata_records



def extract_sql_placeholders(
    sql_template: str,
) -> List[str]:
    """
    Extract placeholder names from SQL.

    Example:
        WHERE BUYER_NAME LIKE '%{buyer_name}%'
          AND CHILD_ITEM LIKE '%{item}%'

    Result:
        ["buyer_name", "item"]
    """

    placeholders = re.findall(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
        sql_template or "",
    )

    return sorted(
        {
            placeholder.strip().lower()
            for placeholder in placeholders
            if placeholder.strip()
        }
    )


def extract_json_object(response_text: str) -> Dict[str, Any]:
    """
    Extract and parse a JSON object returned by the LLM.
    """

    cleaned_response = clean_prompt_text(
        response_text
    )

    cleaned_response = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned_response,
        flags=re.IGNORECASE,
    )

    cleaned_response = re.sub(
        r"\s*```$",
        "",
        cleaned_response,
    ).strip()

    try:
        return json.loads(cleaned_response)

    except json.JSONDecodeError:
        json_match = re.search(
            r"\{.*\}",
            cleaned_response,
            flags=re.DOTALL,
        )

        if not json_match:
            raise ValueError(
                "The LLM response did not contain a valid JSON object."
            )

        return json.loads(
            json_match.group(0)
        )


def validate_generated_sql(
    sql_template: str,
    variable_names: List[str],
) -> Dict[str, Any]:
    """
    Apply basic deterministic validation to the generated SQL.
    """

    cleaned_sql = clean_prompt_text(
        sql_template
    )

    if not cleaned_sql:
        return {
            "status": "error",
            "error_type": "empty_sql",
            "message": "The generated SQL template is empty.",
        }

    normalized_start = cleaned_sql.lstrip().upper()

    if not (
        normalized_start.startswith("SELECT")
        or normalized_start.startswith("WITH")
    ):
        return {
            "status": "error",
            "error_type": "invalid_sql_type",
            "message": (
                "The generated SQL must begin with SELECT or WITH."
            ),
        }

    forbidden_pattern = re.compile(
        r"\b("
        r"INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|"
        r"TRUNCATE|CREATE|GRANT|REVOKE|EXECUTE|"
        r"BEGIN|DECLARE|COMMIT|ROLLBACK"
        r")\b",
        flags=re.IGNORECASE,
    )

    forbidden_match = forbidden_pattern.search(
        cleaned_sql
    )

    if forbidden_match:
        return {
            "status": "error",
            "error_type": "forbidden_sql_keyword",
            "message": (
                "The generated SQL contains a forbidden keyword: "
                f"{forbidden_match.group(1)}"
            ),
        }

    expected_placeholders = sorted(
        {
            str(variable_name).strip().lower()
            for variable_name in variable_names
            if str(variable_name).strip()
        }
    )

    generated_placeholders = extract_sql_placeholders(
        cleaned_sql
    )

    if generated_placeholders != expected_placeholders:
        return {
            "status": "error",
            "error_type": "placeholder_mismatch",
            "message": (
                "The SQL placeholders do not match the required "
                "variable names."
            ),
            "expected_placeholders": expected_placeholders,
            "generated_placeholders": generated_placeholders,
        }

    return {
        "status": "success",
        "sql_template": cleaned_sql,
        "expected_placeholders": expected_placeholders,
        "generated_placeholders": generated_placeholders,
    }


async def generate_sql_from_few_shots(
    new_question: str,
    examples: List[Dict[str, Any]],
    variable_names: List[str],
    client,
    semaphore,
    model: str = "gpt-5-mini",
    recovery_hint: Optional[str] = None,
    metadata_repository=None,
    allowed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate one Oracle SQL template from ranked FAISS examples.

    Parameters
    ----------
    new_question:
        Normalized business question with literal variable values removed.
        Exact variable identity is supplied separately through variable_names,
        which is authoritative for the required SQL placeholders.

        Example:
            "Show inventory for buyer name and item"

    examples:
        Ranked FAISS results. The first example must be the most
        similar result.

        Expected structure:

            [
                {
                    "display_question_template": "...",
                    "sql_template": "..."
                },
                {
                    "display_question_template": "...",
                    "sql_template": "..."
                }
            ]

    variable_names:
        Variable names already prepared in primary_workflow.

        Example:
            ["buyer_name", "item"]

    pool:
        Existing OpenAIPool instance.

    model:
        OpenAI model name.

    Returns
    -------
    Success:

        {
            "status": "success",
            "action": "reuse" or "adapt",
            "selected_example": 1,
            "sql_template": "...",
            "variable_names": [...],
            "example_count": 3
        }
    """

    cleaned_question = clean_prompt_text(
        new_question
    )

    if not cleaned_question:
        return {
            "status": "error",
            "error_type": "missing_question",
            "message": "new_question cannot be empty.",
            "sql_template": "",
        }

    normalized_variable_names = sorted(
        {
            str(variable_name).strip().lower()
            for variable_name in variable_names or []
            if str(variable_name).strip()
        }
    )

    prepared_examples = prepare_few_shot_examples(
        examples
    )

    if not prepared_examples:
        return {
            "status": "error",
            "error_type": "missing_examples",
            "message": (
                "No valid display-question and SQL examples "
                "were provided."
            ),
            "sql_template": "",
        }

    required_placeholders = [
        f"{{{variable_name}}}"
        for variable_name in normalized_variable_names
    ]

    required_placeholders_text = (
        ", ".join(required_placeholders)
        if required_placeholders
        else "NONE - the SQL must contain no placeholders"
    )

    examples_text = build_few_shot_examples_text(
        prepared_examples
    )
    
    # Fetch Weaviate metadata before the existing LLM request.
    weaviate_metadata = await fetch_weaviate_metadata(
        metadata_repository=metadata_repository,
        allowed_tables=allowed_tables,
    )

    weaviate_metadata_text = (
        json.dumps(
            weaviate_metadata,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        if weaviate_metadata
        else "No Weaviate metadata available."
    )
    print("Allowed tables:", allowed_tables)
    print("Metadata count:", len(weaviate_metadata))
    print("Sample metadata:", weaviate_metadata[:3])


    system_prompt = """
You are a constrained Oracle SQL template generator.

You will receive:
1. A new question.
2. The exact required variable placeholders.
3. Ranked display-question and SQL-template examples.

The examples are ordered from most relevant to least relevant.

Follow these rules exactly:

1. Identify the example closest to the new question.
2. The new question is normalized and does not contain literal variable
   values. The separately supplied required placeholders are the authoritative
   variable identities. Use exactly those placeholders in SQL.
3. If that example already answers the question, reuse its SQL.
4. Otherwise, adapt the closest SQL with the smallest possible change.
5. Do not combine unrelated business concepts from different examples.
6. Use only tables, columns, functions, expressions, joins and SQL
   patterns that appear in the supplied examples.
7. Do not invent tables or columns.
8. Generate exactly one Oracle read-only SELECT query.
9. A WITH clause is allowed only when it leads to a SELECT query.
10. Never generate INSERT, UPDATE, DELETE, MERGE, DROP, ALTER,
   TRUNCATE, CREATE or PL/SQL.
   
11. Weaviate metadata is additional reference data, not a replacement for the FAQ examples.
12. If action is "reuse", ignore the Weaviate metadata and preserve the selected FAQ example's SQL logic.
13. If action is "adapt", use the supplied Weaviate metadata to verify relevant tables, columns, data types and descriptions, and make only the minimum necessary changes.
14. Do not introduce unsupported tables or columns. Preserve all existing SQL safety rules and required placeholders.

15. Do not include actual user values.
16. Use exactly the required placeholders.
17. Do not add, remove or rename placeholders.
18. Do not explain the SQL.
19. Do not use markdown or code fences.
20. Return only a valid JSON object.

Required JSON format:

{
  "action": "reuse",
  "selected_example": 1,
  "sql_template": "SELECT ..."
}

The action must be either "reuse" or "adapt".
selected_example must be the number of the primary example used.
""".strip()

    cleaned_recovery_hint = clean_prompt_text(
        recovery_hint
    )

    recovery_hint_section = (
        f"\n\nPREVIOUS ATTEMPT GUIDANCE\n\n{cleaned_recovery_hint}\n"
        if cleaned_recovery_hint
        else ""
    )

    user_prompt = f"""
NEW QUESTION

{cleaned_question}


REQUIRED VARIABLE PLACEHOLDERS

{required_placeholders_text}


RANKED FAQ EXAMPLES

{examples_text}
{recovery_hint_section}

WEAVIATE METADATA REFERENCE
Use this metadata only if the selected action is "adapt".
If the selected action is "reuse", ignore this entire metadata section.

Return the required JSON object.
""".strip()

    try:

        async with semaphore:
            response = await client.responses.create(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
            )

        response_text = clean_prompt_text(
            response.output_text
        )

        llm_result = extract_json_object(
            response_text
        )

    except Exception as error:
        return {
            "status": "error",
            "error_type": "llm_generation_failed",
            "message": str(error),
            "sql_template": "",
        }

    action = clean_prompt_text(
        llm_result.get("action")
    ).lower()

    selected_example = llm_result.get(
        "selected_example"
    )

    sql_template = clean_prompt_text(
        llm_result.get("sql_template")
    )

    if action not in {"reuse", "adapt"}:
        return {
            "status": "error",
            "error_type": "invalid_action",
            "message": (
                "The LLM action must be either reuse or adapt."
            ),
            "llm_result": llm_result,
            "sql_template": sql_template,
        }

    if not isinstance(selected_example, int):
        return {
            "status": "error",
            "error_type": "invalid_selected_example",
            "message": (
                "selected_example must be an integer."
            ),
            "llm_result": llm_result,
            "sql_template": sql_template,
        }

    if not (
        1 <= selected_example <= len(prepared_examples)
    ):
        return {
            "status": "error",
            "error_type": "selected_example_out_of_range",
            "message": (
                "selected_example is outside the supplied "
                "example range."
            ),
            "selected_example": selected_example,
            "example_count": len(prepared_examples),
            "sql_template": sql_template,
        }

    validation_result = validate_generated_sql(
        sql_template=sql_template,
        variable_names=normalized_variable_names,
    )

    if validation_result.get("status") != "success":
        return {
            "status": "error",
            "error_type": validation_result.get(
                "error_type",
                "sql_validation_failed",
            ),
            "message": validation_result.get(
                "message",
                "Generated SQL validation failed.",
            ),
            "action": action,
            "selected_example": selected_example,
            "sql_template": sql_template,
            "expected_placeholders": validation_result.get(
                "expected_placeholders",
                normalized_variable_names,
            ),
            "generated_placeholders": validation_result.get(
                "generated_placeholders",
                [],
            ),
        }

    return {
        "status": "success",
        "action": action,
        "selected_example": selected_example,
        "sql_template": validation_result[
            "sql_template"
        ],
        "variable_names": normalized_variable_names,
        "generated_placeholders": validation_result[
            "generated_placeholders"
        ],
        "example_count": len(prepared_examples),
    }



def expand_multi_value_like_predicates(
    sql_template: str,
    variable_values: dict[str, Any],
) -> str:
    """
    Duplicate LIKE predicates when a variable has multiple values.

    This function does not inject actual values and does not rename
    placeholders.

    Example:

        CHILD_ITEM LIKE '%{item}%'

    becomes:

        (
            CHILD_ITEM LIKE '%{item}%'
            OR CHILD_ITEM LIKE '%{item}%'
        )

    when item has two values.
    """

    expanded_sql = sql_template

    for variable_name, values in variable_values.items():
        if not isinstance(values, list):
            continue

        valid_values = [
            value
            for value in values
            if value is not None
            and str(value).strip()
        ]

        value_count = len(valid_values)

        if value_count <= 1:
            continue

        pattern = re.compile(
            rf"""
            (?P<column>
                [A-Za-z_][A-Za-z0-9_.$]*
            )
            \s+LIKE\s+
            '
            (?P<prefix>[^']*)
            \{{{re.escape(variable_name)}\}}
            (?P<suffix>[^']*)
            '
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        )

        def build_repeated_predicates(
            match: re.Match,
        ) -> str:
            column_name = match.group("column")
            prefix = match.group("prefix")
            suffix = match.group("suffix")

            predicate = (
                f"{column_name} LIKE "
                f"'{prefix}{{{variable_name}}}{suffix}'"
            )

            repeated_predicates = [
                predicate
                for _ in range(value_count)
            ]

            return (
                "(\n        "
                + "\n        OR ".join(repeated_predicates)
                + "\n    )"
            )

        expanded_sql, replacement_count = pattern.subn(
            build_repeated_predicates,
            expanded_sql,
            count=1,
        )

        if replacement_count == 0:
            raise ValueError(
                f"Could not find a LIKE predicate for "
                f"multi-value variable '{variable_name}'."
            )

    return expanded_sql


async def generate_sql_with_multi_value_expansion(
    new_question: str,
    examples: List[Dict[str, Any]],
    variable_names: List[str],
    variable_values: Dict[str, Any],
    client,
    semaphore,
    model: str = "gpt-5-mini",
    recovery_hint: Optional[str] = None,
    metadata_repository=None,
    allowed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate SQL using few-shot examples and expand LIKE predicates
    when any variable contains multiple values.

    The original variable names are preserved.

    Example:

        variable_values = {
            "buyer_name": "Aditya",
            "item": [
                "123.44.55",
                "456.77.88",
            ],
        }

    Base generated SQL:

        CHILD_ITEM LIKE '%{item}%'

    Expanded SQL:

        (
            CHILD_ITEM LIKE '%{item}%'
            OR CHILD_ITEM LIKE '%{item}%'
        )
    """

    generation_result = await generate_sql_from_few_shots(
        new_question=new_question,
        examples=examples,
        variable_names=variable_names,
        client=client,
        semaphore=semaphore,
        model=model,
        recovery_hint=recovery_hint,
        metadata_repository=metadata_repository,
        allowed_tables=allowed_tables,
    )

    if generation_result.get("status") != "success":
        return generation_result

    has_multi_value_variable = any(
        isinstance(value, list)
        and len(
            [
                item
                for item in value
                if item is not None
                and str(item).strip()
            ]
        ) > 1
        for value in (variable_values or {}).values()
    )

    if not has_multi_value_variable:
        generation_result["multi_value_expansion_applied"] = False
        return generation_result

    generated_sql = clean_prompt_text(
        generation_result.get("sql_template")
    )

    if not generated_sql:
        return {
            **generation_result,
            "status": "error",
            "error_type": "missing_generated_sql",
            "message": (
                "SQL generation succeeded, but sql_template "
                "was empty."
            ),
            "multi_value_expansion_applied": False,
        }

    try:
        expanded_sql = expand_multi_value_like_predicates(
            sql_template=generated_sql,
            variable_values=variable_values,
        )

    except Exception as error:
        return {
            **generation_result,
            "status": "error",
            "error_type": "multi_value_expansion_failed",
            "message": str(error),
            "original_sql_template": generated_sql,
            "multi_value_expansion_applied": False,
        }

    generation_result["original_sql_template"] = generated_sql
    generation_result["sql_template"] = expanded_sql
    generation_result["multi_value_expansion_applied"] = True

    generation_result["multi_value_variables"] = {
        variable_name: len(
            [
                item
                for item in values
                if item is not None
                and str(item).strip()
            ]
        )
        for variable_name, values in variable_values.items()
        if isinstance(values, list)
        and len(
            [
                item
                for item in values
                if item is not None
                and str(item).strip()
            ]
        ) > 1
    }

    return generation_result



# ============================================================================================================================================================================================================================================================================================
# MAIN
# ============================================================================================================================================================================================================================================================================================
async def main():
    new_question = (
        "Show inventory for buyer and item"
    )

    variable_names = [
        "buyer_name",
        "item",
    ]

    faiss_options = [
        {
            "display_question_template": (
                "Show today's total stock of item {item} "
                "for buyer {buyer_name}"
            ),
            "sql_template": """
SELECT
    CHILD_ITEM,
    BUYER_NAME,
    NVL(TODAYS_STOCKS_ITEM_COUNT, 0) AS CURRENT_STOCK
FROM KPCL_REPORTING.KPC_FULL_KIT_INV_SUMMARY
WHERE CHILD_ITEM LIKE '%{item}%'
  AND BUYER_NAME LIKE '%{buyer_name}%'
""".strip(),
        },
        {
            "display_question_template": (
                "Check buyer {buyer_name} safety stock "
                "for item {item}"
            ),
            "sql_template": """
SELECT
    BUYER_NAME,
    CHILD_ITEM,
    NVL(SAFETY_STOCK, 0) AS SAFETY_STOCK
FROM KPCL_REPORTING.KPC_FULL_KIT_INV_SUMMARY
WHERE BUYER_NAME LIKE '%{buyer_name}%'
  AND CHILD_ITEM LIKE '%{item}%'
""".strip(),
        },
        {
            "display_question_template": (
                "View excess quantity and value for item "
                "{item} under buyer {buyer_name}"
            ),
            "sql_template": """
SELECT
    BUYER_NAME,
    CHILD_ITEM,
    SUM(NVL(REMAINING_QTY, 0)) AS TOTAL_EXCESS_QTY,
    SUM(
        NVL(REMAINING_QTY, 0)
        * NVL(ITEM_COST, 0)
    ) AS TOTAL_EXCESS_VALUE
FROM KPCL_REPORTING.KPC_FULL_KIT_INV_SUMMARY
WHERE CHILD_ITEM LIKE '%{item}%'
  AND BUYER_NAME LIKE '%{buyer_name}%'
GROUP BY
    BUYER_NAME,
    CHILD_ITEM
""".strip(),
        },
    ]

    pool = OpenAIPool()

    result = await generate_sql_from_few_shots(
        new_question=new_question,
        examples=faiss_options,
        variable_names=variable_names,
        pool=pool,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())