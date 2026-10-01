from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InboundMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    event_id: str
    source_message_id: str
    device_id: str
    sender: str
    text: str
    received_at: datetime


class ConversationTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: str
    assistant: str


class AiRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    conversation_id: UUID
    turn_id: int = Field(gt=0)
    user_id: str
    message: str
    language: str
    history: tuple[ConversationTurn, ...] = Field(default=(), max_length=5)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AiResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    answer: str

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be blank")
        return value
