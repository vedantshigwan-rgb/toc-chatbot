from __future__ import annotations

import os
import re
from typing import Any, Optional


class FeedbackRewriteServiceError(RuntimeError):
    """Raised only when the rewrite model cannot produce a usable question."""

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


_SYSTEM_PROMPT = """
You rewrite feedback into one new normalized business question.

You receive:
1. the previous normalized business question
2. the current normalized feedback text
3. the names of the structured variables already owned by the backend

Your job is ONLY to rewrite the business meaning into one complete normalized
question that represents the previous request after applying the feedback.

Rules:
- Preserve previous requirements unless the feedback changes them.
- Resolve references such as "this item", "same buyer", "for this", "instead",
  "top 5", and similar follow-up wording from the previous normalized question.
- Do not output SQL.
- Do not output JSON.
- Do not output markdown.
- Do not output variable values.
- Do not output /variable {value} syntax.
- Do not invent database tables, columns, values, or new variables.
- Keep the wording concise and suitable for FAQ/FAISS retrieval.
- Return exactly one plain-text normalized question and nothing else.
""".strip()


class FeedbackRewriteService:
    """
    One-step semantic feedback rewriter.

    This service never decides variable values and never reconstructs frontend
    variable syntax. Python keeps variable state separately. The only model
    output is a plain-text normalized question.
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
                "FEEDBACK_REWRITE_MODEL",
                "gpt-5-mini",
            )
        )

    async def rewrite(
        self,
        *,
        previous_normalized_question: str,
        normalized_feedback: str,
        previous_variable_names: list[str],
        current_variable_names: list[str],
    ) -> str:
        previous = str(
            previous_normalized_question or ""
        ).strip()
        feedback = str(normalized_feedback or "").strip()

        if not previous:
            raise FeedbackRewriteServiceError(
                "The previous normalized question is unavailable.",
                code="previous_normalized_question_required",
            )

        if not feedback:
            raise FeedbackRewriteServiceError(
                "The feedback text is empty after normalization.",
                code="empty_normalized_feedback",
            )

        previous_names = self._clean_names(
            previous_variable_names
        )
        current_names = self._clean_names(
            current_variable_names
        )

        user_prompt = (
            "PREVIOUS NORMALIZED QUESTION:\n"
            f"{previous}\n\n"
            "CURRENT NORMALIZED FEEDBACK:\n"
            f"{feedback}\n\n"
            "PREVIOUS STRUCTURED VARIABLE NAMES:\n"
            f"{', '.join(previous_names) if previous_names else 'none'}\n\n"
            "CURRENT FEEDBACK VARIABLE NAMES:\n"
            f"{', '.join(current_names) if current_names else 'none'}"
        )

        try:
            client = await self.pool.get_client()
        except Exception as error:
            raise FeedbackRewriteServiceError(
                "Could not acquire the OpenAI client for feedback rewriting.",
                code="feedback_rewrite_client_failed",
                details={
                    "exception_type": type(error).__name__,
                    "exception_message": str(error),
                },
            ) from error

        try:
            async with self.pool.semaphore:
                response = await client.responses.create(
                    model=self.model,
                    instructions=_SYSTEM_PROMPT,
                    input=user_prompt,
                )
        except Exception as error:
            raise FeedbackRewriteServiceError(
                "The feedback rewrite model request failed.",
                code="feedback_rewrite_model_failed",
                details={
                    "exception_type": type(error).__name__,
                    "exception_message": str(error),
                },
            ) from error

        raw_text = str(
            getattr(response, "output_text", "") or ""
        ).strip()

        rewritten = self._clean_model_text(raw_text)

        if not rewritten:
            raise FeedbackRewriteServiceError(
                "The feedback rewrite model returned an empty question.",
                code="empty_feedback_rewrite",
                details={"raw_response": raw_text},
            )

        if len(rewritten) > 1500:
            raise FeedbackRewriteServiceError(
                "The feedback rewrite model returned an unusably long question.",
                code="feedback_rewrite_too_long",
                details={
                    "response_length": len(rewritten),
                    "raw_response": raw_text,
                },
            )

        return rewritten

    @staticmethod
    def _clean_names(values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()

        for value in values or []:
            name = str(value or "").strip().lower()
            if not name or name in seen:
                continue
            cleaned.append(name)
            seen.add(name)

        return cleaned

    @staticmethod
    def _clean_model_text(value: Any) -> str:
        """
        Tolerate harmless presentation noise without requiring a JSON schema.
        """
        text = str(value or "").strip()

        text = re.sub(
            r"^```(?:text|plaintext)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s*```$", "", text).strip()

        text = re.sub(
            r"^(?:rewritten|normalized)(?:\s+question)?\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        if len(text) >= 2 and (
            (text[0] == text[-1] == '"')
            or (text[0] == text[-1] == "'")
        ):
            text = text[1:-1].strip()

        return re.sub(r"\s+", " ", text).strip()
