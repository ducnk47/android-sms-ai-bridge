from typing import Protocol

from sms_bridge.domain.models import AiRequest, AiResponse


class AiAdapter(Protocol):
    async def answer(self, request: AiRequest) -> AiResponse: ...
