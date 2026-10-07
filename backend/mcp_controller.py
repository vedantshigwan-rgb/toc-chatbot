from __future__ import annotations

from typing import Optional

from core.models import PreparedRequest
from utils.openai_pool import OpenAIPool
from utils.search_faiss_indexes import FAQIndexSearcher
from workflows.primary_workflow import PrimaryWorkflow


async def primary_workflow(
    user_input: str,
    report_name: str,
    variables: list,
    email: str,
    pool: OpenAIPool,
    faq_searcher: FAQIndexSearcher,
    workflow_store,
    metadata_repository=None,
    session_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    user_role: Optional[str] = None,
    request_db_execution_tracker=None,
    recovery_hint: Optional[str] = None,
    prepared_request: Optional[PreparedRequest] = None,
) -> dict:
    """Single business-data workflow entry point."""
    workflow = PrimaryWorkflow(
        pool=pool,
        faq_searcher=faq_searcher,
        workflow_store=workflow_store,
        metadata_repository=metadata_repository,
    )
    return await workflow.execute(
        user_input=user_input,
        report_name=report_name,
        variables=variables,
        email=email,
        session_id=session_id,
        chat_id=chat_id,
        user_role=user_role,
        request_db_execution_tracker=request_db_execution_tracker,
        recovery_hint=recovery_hint,
        prepared_request=prepared_request,
    )
