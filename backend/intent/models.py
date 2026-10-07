from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntentType(str, Enum):
    """Only intents the base LLM is allowed to decide."""

    GREETING = "greeting"
    DATA_QUERY = "data_query"
    UNKNOWN = "unknown"


class ConversationMessage(BaseModel):
    """Retained only for API compatibility; normal intent routing ignores memory."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Conversation message content cannot be empty.")
        return cleaned


class IntentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    intent: IntentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=300)
    context_used: bool = False
    is_ambiguous: bool = Field(default=False, strict=True)
    possible_intents: list[IntentType] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Classification reason cannot be empty.")
        return cleaned

    @model_validator(mode="after")
    def validate_ambiguity(self) -> "IntentClassification":
        if len(set(self.possible_intents)) != len(self.possible_intents):
            raise ValueError("possible_intents must not contain duplicates.")
        if self.is_ambiguous:
            if len(self.possible_intents) < 2:
                raise ValueError("is_ambiguous=True requires at least two possible_intents.")
            if self.intent not in self.possible_intents:
                raise ValueError("possible_intents must include the selected intent.")
        elif self.possible_intents:
            self.possible_intents = []
        return self
