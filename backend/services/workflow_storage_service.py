from __future__ import annotations

from typing import Any, Dict, List, Optional


async def create_primary_workflow_record(
    *,
    workflow_store,
    workflow_id: str,
    session_id: Optional[str],
    chat_id: Optional[str],
    email: str,
    user_role: Optional[str],
    report_name: str,
    user_input: str,
    query_without_values: str,
    variable_names: List[str],
    variables_key: str,
    variable_values: Dict[str, Any],
    suggestions: List[Dict[str, Any]],
) -> None:
    """
    Create the initial MongoDB workflow record.

    Exceptions are deliberately allowed to propagate so the
    controller can convert them into the common workflow contract.
    """

    await workflow_store.create_workflow(
        workflow_id=workflow_id,
        session_id=session_id,
        chat_id=chat_id,
        user_id=email,
        user_role=user_role,
        report_name=report_name,
        user_input=user_input,
        query_without_values=query_without_values,
        variable_names=variable_names,
        variables_key=variables_key,
        variables=variable_values,
        suggestions=suggestions,
    )


async def safe_store_generation_error(
    *,
    workflow_store,
    workflow_id: str,
    error_message: str,
) -> Optional[str]:
    """
    Store a generation error without allowing a MongoDB failure
    to hide the actual workflow failure.

    Returns:
        None when storage succeeds.
        The storage error message when storage fails.
    """

    try:
        await workflow_store.store_fallback_error(
            workflow_id=workflow_id,
            error_message=error_message,
        )
        return None

    except Exception as storage_error:
        return str(storage_error)


async def safe_store_generation_result(
    *,
    workflow_store,
    workflow_id: str,
    generation_result: Dict[str, Any],
) -> Optional[str]:
    """
    Store SQL-generation or execution information safely.

    Returns:
        None when storage succeeds.
        The storage error message when storage fails.
    """

    try:
        await workflow_store.store_fallback_result(
            workflow_id=workflow_id,
            fallback_result=generation_result,
        )
        return None

    except Exception as storage_error:
        return str(storage_error)