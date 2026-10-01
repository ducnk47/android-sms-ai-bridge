import asyncio
from datetime import UTC, datetime

from sms_bridge.services.message_processor import MessageProcessor
from sms_bridge.storage.sqlite import SqliteStore


class MessageWorker:
    def __init__(
        self,
        store: SqliteStore,
        processor: MessageProcessor,
        *,
        poll_interval_seconds: float = 0.25,
        concurrency: int = 4,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")
        self._store = store
        self._processor = processor
        self._poll_interval_seconds = poll_interval_seconds
        self._concurrency = concurrency
        self._stop_event = asyncio.Event()

    async def run_once(self, now: datetime) -> bool:
        work = await self._store.claim_next(now)
        if work is None:
            return False
        await self._processor.process(work)
        return True

    async def run(self) -> None:
        await self._store.recover_stale(datetime.now(UTC))
        loops = [
            asyncio.create_task(self._poll_loop(), name=f"sms-worker-{number}")
            for number in range(self._concurrency)
        ]
        try:
            await self._stop_event.wait()
        finally:
            self._stop_event.set()
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)

    async def stop(self) -> None:
        self._stop_event.set()

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = await self.run_once(datetime.now(UTC))
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                pass
