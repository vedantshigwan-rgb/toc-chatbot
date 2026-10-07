from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.few_shot_sql_generator import (
    generate_sql_with_multi_value_expansion,
)


async def generate_primary_sql(
    *,
    new_question: str,
    examples: List[Dict[str, Any]],
    variable_names: List[str],
    variable_values: Dict[str, Any],
    client,
    semaphore,
    recovery_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate or select SQL using the primary few-shot workflow.

    Exceptions are allowed to propagate so the workflow controller
    can produce the standard WorkflowResult error contract.

    `recovery_hint` is optional (Step 23). When supplied by a bounded
    static semantic recovery attempt, it is passed straight through to
    the LLM prompt only - it never touches FAQ search/retrieval, which
    already completed before this function is called.
    """

    generation_result = (
        await generate_sql_with_multi_value_expansion(
            new_question=new_question,
            examples=examples,
            variable_names=variable_names,
            variable_values=variable_values,
            client=client,
            semaphore=semaphore,
            recovery_hint=recovery_hint,
        )
    )

    if not isinstance(generation_result, dict):
        raise TypeError(
            "Primary SQL generation returned an invalid "
            f"response type: {type(generation_result).__name__}"
        )

    return generation_result