from __future__ import annotations

import json
import os
from typing import Any, Optional


class MetadataResponseServiceError(RuntimeError):
    """
    Raised when the metadata response model
    cannot produce a valid user-facing answer.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[
            dict[str, Any]
        ] = None,
    ) -> None:

        super().__init__(message)

        self.code = code
        self.details = details or {}


class MetadataResponseService:
    """
    Converts Weaviate ColumnMetadata search results
    into a clean user-facing explanation.

    IMPORTANT:

    This service does NOT:
    - query Oracle
    - generate SQL
    - use YAML
    - use FAISS
    - call Primary
    - call Secondary

    It only receives already-retrieved metadata
    and explains it to the user.
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
                "METADATA_RESPONSE_MODEL",
                "gpt-5-mini",
            )
        )

    async def respond(
        self,
        *,
        user_input: str,
        report_name: str,
        metadata_records: list[
            dict[str, Any]
        ],
    ) -> str:
        """
        Generate a grounded explanation using only
        the supplied ColumnMetadata records.
        """

        cleaned_input = str(
            user_input or ""
        ).strip()

        cleaned_report = str(
            report_name or ""
        ).strip()

        if not cleaned_input:
            raise MetadataResponseServiceError(
                "User input cannot be empty.",
                code="empty_user_input",
            )

        if not cleaned_report:
            raise MetadataResponseServiceError(
                "Report name cannot be empty.",
                code="empty_report_name",
            )

        if not metadata_records:
            return (
                "I could not find enough metadata "
                f"in {cleaned_report} to answer "
                "that confidently."
            )

        system_prompt = """
The metadata records are ordered by vector similarity,
but vector rank is only retrieval evidence. Do not
automatically assume the first record is the correct one.

Analyze ALL supplied metadata records together with the
user's wording and determine which record, or combination
of records, best answers the question.

Selection rules:

- Compare the user's wording against every candidate.
- Use the column name, table name, description, text,
  datatype, units, filterability, and other available
  metadata together.
- Prefer the record whose business meaning most directly
  matches the user's question.
- Do not prefer a more specialized or derived field only
  because it ranks higher semantically.
- If one candidate represents the direct/base meaning and
  others represent more specific variants, explain the
  direct/base meaning first and mention the variants only
  when they help clarify the answer.
- If multiple records are genuinely relevant, combine
  them into one clear explanation.
- If the candidates represent different meanings, explain
  the distinction rather than forcing a single choice.
- If the metadata is insufficient or ambiguous, say that
  clearly.
- Never invent metadata that is not present in the supplied
  records.
""".strip()

        metadata_json = json.dumps(
            metadata_records,
            ensure_ascii=False,
            default=str,
            indent=2,
        )

        user_prompt = (
            "Selected report:\n"
            f"{cleaned_report}\n\n"
            "User question:\n"
            f"{cleaned_input}\n\n"
            "Retrieved ColumnMetadata records "
            "(best matches first):\n"
            f"{metadata_json}"
        )

        try:
            client = (
                await self.pool.get_client()
            )

        except Exception as error:
            raise MetadataResponseServiceError(
                (
                    "Could not acquire the "
                    "OpenAI client."
                ),
                code=(
                    "client_acquisition_failed"
                ),
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
                        instructions=(
                            system_prompt
                        ),
                        input=user_prompt,
                    )
                )

        except Exception as error:
            raise MetadataResponseServiceError(
                (
                    "Metadata response model "
                    "request failed."
                ),
                code=(
                    "metadata_response_model_failed"
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
            raise MetadataResponseServiceError(
                (
                    "Metadata response model "
                    "returned an empty response."
                ),
                code=(
                    "empty_metadata_response"
                ),
            )

        return output_text