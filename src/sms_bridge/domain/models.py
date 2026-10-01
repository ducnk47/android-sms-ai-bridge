from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
