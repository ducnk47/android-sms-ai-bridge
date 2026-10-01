from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SmsSendResult:
    message_id: str
    accepted: bool


class SmsGatewayError(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class SmsGateway(Protocol):
    async def send(self, request_id: UUID, recipient: str, text: str) -> SmsSendResult: ...

    async def health(self) -> bool: ...
