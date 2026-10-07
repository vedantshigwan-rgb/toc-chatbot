from __future__ import annotations

import json
import os
from typing import Any, Optional

from services.help_retrieval_service import (
    HelpRetrievalError,
    HelpRetrievalService,
)


class HelpServiceError(
    RuntimeError
):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[
            dict[str, Any]
        ] = None,
    ) -> None:

        super().__init__(
            message
        )

        self.code = code
        self.details = (
            details or {}
        )


class HelpService:
    """
    Dedicated user-guide handler.

    Retrieval:
        Help FAISS

    Generation:
        LLM grounded only in retrieved
        user-guide content.
    """

    def __init__(
        self,
        *,
        pool: Any,
        retrieval_service: (
            HelpRetrievalService
        ),
        model: Optional[str] = None,
    ) -> None:

        self.pool = pool

        self.retrieval_service = (
            retrieval_service
        )

        self.model = (
            model
            or os.getenv(
                "HELP_MODEL",
                "gpt-4.1-mini",
            )
        )

    async def respond(
        self,
        *,
        user_input: str,
        report_name: Optional[str] = None,
    ) -> tuple[
        str,
        list[dict[str, Any]],
    ]:

        cleaned_input = str(
            user_input or ""
        ).strip()

        if not cleaned_input:
            raise HelpServiceError(
                (
                    "Help request "
                    "cannot be empty."
                ),
                code=(
                    "empty_help_request"
                ),
            )

        cleaned_report = str(report_name or "").strip()

        retrieval_query = (
            f"Report: {cleaned_report}\nQuestion: {cleaned_input}"
            if cleaned_report
            else cleaned_input
        )

        try:

            matches = (
                await self
                .retrieval_service
                .search(
                    query=retrieval_query,
                )
            )

        except HelpRetrievalError as error:
            raise HelpServiceError(
                (
                    "Help knowledge retrieval "
                    "failed."
                ),
                code=error.code,
                details=error.details,
            ) from error

        if not matches:

            return (
                (
                    "I couldn't find relevant "
                    "instructions for that in "
                    "the available user guide."
                ),
                [],
            )

        context_items = []

        for match in matches:

            context_items.append(
                {
                    "id": (
                        match.get("id")
                    ),
                    "title": (
                        match.get("title")
                    ),
                    "content": (
                        match.get(
                            "content"
                        )
                    ),
                }
            )

        system_prompt = """
You are the user-guide response layer of a
general-purpose application.

The user's message has already been classified
as an application-help request.

You will receive retrieved user-guide material.

Your job is to answer the user's question using
ONLY that retrieved material.

RULES:

1. Use only the supplied user-guide context.

2. Do not use general world knowledge to fill
   missing information.

3. Do not invent application capabilities.

4. Do not invent interface controls or workflow
   behaviour.

5. Do not assume any particular business domain,
   database, schema, report, field, entity, or
   connected data source.

6. If the supplied material does not answer the
   question, clearly state that the available
   user guide does not contain enough information.

7. Answer directly and concisely.

8. Do not mention FAISS, embeddings, vector
   search, prompts, internal tools, models,
   intent classification, or internal workflow
   architecture.

9. Do not generate SQL.

10. Return only the user-facing response.
""".strip()

        payload = {
            "selected_report": cleaned_report or None,
            "user_question": cleaned_input,
            "retrieved_user_guide": context_items,
        }

        try:

            client = (
                await self.pool.get_client()
            )

            async with (
                self.pool.semaphore
            ):

                response = (
                    await client.responses.create(
                        model=self.model,
                        instructions=(
                            system_prompt
                        ),
                        input=json.dumps(
                            payload,
                            ensure_ascii=False,
                        ),
                        store=False,
                    )
                )

        except Exception as error:
            raise HelpServiceError(
                (
                    "Help response generation "
                    "failed."
                ),
                code=(
                    "help_generation_failed"
                ),
                details={
                    "exception_type": (
                        type(error).__name__
                    ),
                    "exception_message": (
                        str(error)
                    ),
                },
            ) from error

        output_text = str(
            getattr(
                response,
                "output_text",
                "",
            )
            or ""
        ).strip()

        if not output_text:
            raise HelpServiceError(
                (
                    "Help response model "
                    "returned no content."
                ),
                code=(
                    "empty_help_response"
                ),
            )

        return (
            output_text,
            matches,
        )