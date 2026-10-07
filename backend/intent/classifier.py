from __future__ import annotations

import json
import os
from typing import (
    Any,
    Iterable,
    Optional,
)

from pydantic import ValidationError

from intent.models import (
    ConversationMessage,
    IntentClassification,
)
from intent.prompt import (
    INTENT_CLASSIFIER_SYSTEM_PROMPT,
)


class IntentClassifierError(RuntimeError):
    """
    Raised when the intent classifier cannot return a
    valid structured classification.
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


class IntentClassifier:
    """
    AI-based intent classifier.

    Supported intents:
    - greeting
    - data_query
    - unknown

    Normal-message classification is deliberately context-free.
    Help, metadata, and feedback are explicit request modes handled
    before this classifier is called.
    """

    def __init__(
        self,
        *,
        pool: Any,
        model: Optional[str] = None,
        max_memory_messages: int = 6,
    ) -> None:
        if max_memory_messages < 0:
            raise ValueError(
                "max_memory_messages cannot be negative."
            )

        self.pool = pool

        self.model = model or os.getenv(
            "INTENT_CLASSIFIER_MODEL",
            "gpt-4.1-mini",
        )

        self.max_memory_messages = (
            max_memory_messages
        )

    async def classify(
        self,
        *,
        user_input: str,
        memory: Optional[
            Iterable[
                ConversationMessage
                | dict[str, Any]
            ]
        ] = None,
        quoted_message: Optional[str] = None,
        selected_data_context: Optional[str] = None,
        result_context_available: bool = False,
    ) -> IntentClassification:
        """
        Classify the current user message.
        """

        if not isinstance(user_input, str):
            raise IntentClassifierError(
                "User input must be a string.",
                code="invalid_user_input_type",
                details={
                    "received_type": (
                        type(user_input).__name__
                    ),
                },
            )

        cleaned_input = user_input.strip()

        if not cleaned_input:
            raise IntentClassifierError(
                "User input cannot be empty.",
                code="empty_user_input",
            )

        normalized_memory = self._normalize_memory(
            memory
        )

        cleaned_quoted_message = None

        if (
            isinstance(quoted_message, str)
            and quoted_message.strip()
        ):
            cleaned_quoted_message = (
                quoted_message.strip()
            )


        cleaned_selected_data_context = None

        if (
            isinstance(selected_data_context, str)
            and selected_data_context.strip()
        ):
            cleaned_selected_data_context = (
                selected_data_context.strip()
            )



        # Normal intent routing is intentionally context-free.
        # Conversation memory, quoted messages and result context are NOT
        # sent to the classifier, so it cannot infer feedback/follow-ups.
        payload = {
            "current_user_message": cleaned_input,
            "selected_data_context": cleaned_selected_data_context,
        }

        user_prompt = (
            "Classify the following JSON payload.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
            "\n\n"
            "Return only one valid JSON object using exactly "
            "this structure:\n"
            "{\n"
            '  "intent": "greeting | data_query | unknown",\n'
            '  "confidence": 0.0,\n'
            '  "reason": "Brief factual reason",\n'
            '  "context_used": false,\n'
            '  "is_ambiguous": false,\n'
            '  "possible_intents": []\n'
            "}\n\n"
            "is_ambiguous must be true only when the message "
            "genuinely could belong to more than one supported "
            "intent. possible_intents must be empty when "
            "is_ambiguous is false, and otherwise must list every "
            "genuinely plausible intent (including the one chosen "
            "above).\n"
            "Do not use markdown or code fences."
        )

        try:
            client = await self.pool.get_client()

        except Exception as exc:
            raise IntentClassifierError(
                "Could not acquire the OpenAI client.",
                code="client_acquisition_failed",
                details={
                    "exception_type": (
                        type(exc).__name__
                    ),
                    "exception_message": str(exc),
                },
            ) from exc

        try:
            async with self.pool.semaphore:
                response = (
                    await client.responses.create(
                        model=self.model,
                        instructions=(
                            INTENT_CLASSIFIER_SYSTEM_PROMPT
                        ),
                        input=user_prompt,
                    )
                )

        except Exception as exc:
            raise IntentClassifierError(
                "The intent-classification model "
                "call failed.",
                code="model_call_failed",
                details={
                    "exception_type": (
                        type(exc).__name__
                    ),
                    "exception_message": str(exc),
                    "model": self.model,
                },
            ) from exc

        response_text = getattr(
            response,
            "output_text",
            None,
        )

        if not response_text:
            raise IntentClassifierError(
                "The intent-classification model "
                "returned no output text.",
                code="empty_model_response",
                details={
                    "model": self.model,
                },
            )

        cleaned_response = self._clean_json_response(
            response_text
        )

        try:
            parsed_response = json.loads(
                cleaned_response
            )

        except ValueError as exc:
            raise IntentClassifierError(
                "The model response was not valid JSON.",
                code="invalid_model_json",
                details={
                    "raw_response": response_text,
                    "cleaned_response": (
                        cleaned_response
                    ),
                    "exception_message": str(exc),
                },
            ) from exc

        if not isinstance(parsed_response, dict):
            raise IntentClassifierError(
                "The model response was not a JSON object.",
                code="invalid_model_response",
                details={
                    "raw_response": response_text,
                    "cleaned_response": (
                        cleaned_response
                    ),
                },
            )

        # Step 14: the live classifier contract requires the
        # structured confidence/ambiguity fields explicitly. A real
        # response silently omitting them is a technical parsing
        # failure - it must never fall through to
        # IntentClassification's own lenient defaults, which exist
        # only to keep other, non-classifier callers backward
        # compatible.
        missing_fields = sorted(
            {
                "intent",
                "confidence",
                "reason",
                "context_used",
                "is_ambiguous",
                "possible_intents",
            }
            - parsed_response.keys()
        )

        if missing_fields:
            raise IntentClassifierError(
                "The model response was missing required "
                "classification fields.",
                code="invalid_model_response",
                details={
                    "missing_fields": missing_fields,
                    "raw_response": response_text,
                    "cleaned_response": (
                        cleaned_response
                    ),
                },
            )

        try:
            result = (
                IntentClassification
                .model_validate(
                    parsed_response
                )
            )

        except ValidationError as exc:
            raise IntentClassifierError(
                "The model response did not match "
                "the intent-classification contract.",
                code="invalid_model_response",
                details={
                    "validation_errors": (
                        exc.errors()
                    ),
                    "raw_response": response_text,
                    "cleaned_response": (
                        cleaned_response
                    ),
                },
            ) from exc

        return result

    def _normalize_memory(
        self,
        memory: Optional[
            Iterable[
                ConversationMessage
                | dict[str, Any]
            ]
        ],
    ) -> list[ConversationMessage]:
        """
        Validate conversation memory and retain only the
        latest configured messages.
        """

        if (
            not memory
            or self.max_memory_messages == 0
        ):
            return []

        normalized_messages: list[
            ConversationMessage
        ] = []

        for item in memory:
            if isinstance(
                item,
                ConversationMessage,
            ):
                normalized_messages.append(item)

            else:
                normalized_messages.append(
                    ConversationMessage.model_validate(
                        item
                    )
                )

        return normalized_messages[
            -self.max_memory_messages:
        ]

    @staticmethod
    def _clean_json_response(
        response_text: str,
    ) -> str:
        """
        Remove optional Markdown code fences from the
        model response.
        """

        cleaned_response = response_text.strip()

        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[
                len("```json"):
            ].strip()

        elif cleaned_response.startswith("```"):
            cleaned_response = cleaned_response[
                len("```"):
            ].strip()

        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[
                :-len("```")
            ].strip()

        return cleaned_response