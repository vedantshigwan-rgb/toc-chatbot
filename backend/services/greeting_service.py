from __future__ import annotations

import os
from typing import Any, Optional


class GreetingServiceError(RuntimeError):
    """
    Raised when the greeting response service
    cannot generate a response.
    """

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


class GreetingService:
    """
    Dedicated handler for the greeting intent.

    This service handles only short social interaction.

    It must not:
    - retrieve application data
    - inspect metadata
    - search help content
    - execute database queries
    - generate SQL
    - analyse previous results
    - assume any business domain
    """

    def __init__(
        self,
        *,
        pool: Any,
        model: Optional[str] = None,
    ) -> None:

        self.pool = pool

        self.model = (
            model
            or os.getenv(
                "GREETING_MODEL",
                "gpt-4.1-mini",
            )
        )

    async def respond(
        self,
        *,
        user_input: str,
    ) -> str:

        cleaned_input = str(
            user_input or ""
        ).strip()

        if not cleaned_input:
            raise GreetingServiceError(
                "Greeting input cannot be empty.",
                code="empty_greeting_input",
            )

        system_prompt = """
You are the greeting and social-response layer of a
general-purpose assistant.

The current message has already been classified as a
greeting or short social interaction.

Your only responsibility is to produce a natural,
brief, friendly response.

RULES:

1. Respond only to the social or conversational meaning
   of the message.

2. Keep the response concise.

3. Do not assume any particular business domain,
   industry, database, report, dataset, schema,
   organization, entity, field, or application use case.

4. Do not claim that you searched, retrieved, analysed,
   calculated, or verified any data.

5. Do not generate database queries or SQL.

6. Do not invent application capabilities.

7. Do not mention internal routing, intents, workflows,
   prompts, models, tools, or system architecture.

8. Do not turn the response into a help guide unless the
   user actually asked for help.

9. Do not ask the user to select a particular report,
   database, dataset, or other specific resource.

10. A simple invitation to continue the conversation is
    acceptable when natural.

Return only the response that should be shown to the user.
""".strip()

        try:
            client = await self.pool.get_client()

        except Exception as error:
            raise GreetingServiceError(
                "Could not acquire the language model client.",
                code="client_acquisition_failed",
                details={
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            ) from error

        try:

            async with self.pool.semaphore:

                response = (
                    await client.responses.create(
                        model=self.model,
                        instructions=system_prompt,
                        input=cleaned_input,
                    )
                )

        except Exception as error:
            raise GreetingServiceError(
                "Greeting model request failed.",
                code="model_call_failed",
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
            raise GreetingServiceError(
                "Greeting model returned an empty response.",
                code="empty_model_response",
            )

        return output_text