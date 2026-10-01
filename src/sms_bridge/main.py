import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from sms_bridge.adapters.ai.echo import EchoAiAdapter
from sms_bridge.adapters.sms.fake import FakeSmsGateway
from sms_bridge.adapters.sms.sms_gate import SmsGateLocalClient
from sms_bridge.api.health import router as health_router
from sms_bridge.api.webhooks import development_router
from sms_bridge.api.webhooks import router as webhook_router
from sms_bridge.config import Settings
from sms_bridge.logging import configure_logging
from sms_bridge.services.message_processor import MessageProcessor
from sms_bridge.services.worker import MessageWorker
from sms_bridge.storage.sqlite import SqliteStore


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(include_message_bodies=resolved_settings.log_message_bodies)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = SqliteStore(
            resolved_settings.database_path,
            conversation_ttl_minutes=resolved_settings.conversation_ttl_minutes,
            history_limit=resolved_settings.history_turn_limit,
        )
        await store.initialize()
        app.state.store = store

        http_client: httpx.AsyncClient | None = None
        if resolved_settings.gateway_mode == "local":
            if (
                resolved_settings.gateway_username is None
                or resolved_settings.gateway_password is None
            ):
                raise RuntimeError("Local gateway credentials are required")
            http_client = httpx.AsyncClient()
            gateway = SmsGateLocalClient(
                resolved_settings.gateway_base_url,
                resolved_settings.gateway_username,
                resolved_settings.gateway_password.get_secret_value(),
                http_client,
                priority=resolved_settings.gateway_priority,
            )
        else:
            gateway = FakeSmsGateway()
        app.state.gateway = gateway

        if resolved_settings.ai_mode != "echo":
            raise RuntimeError("HTTP AI mode is not implemented yet")
        app.state.ai = EchoAiAdapter()
        processor = MessageProcessor(
            store,
            app.state.ai,
            gateway,
            ai_timeout_seconds=resolved_settings.ai_timeout_seconds,
            reply_max_chars=resolved_settings.reply_max_chars,
        )
        app.state.processor = processor
        worker = MessageWorker(store, processor)
        app.state.worker = worker
        worker_task: asyncio.Task[None] | None = None
        if start_worker:
            worker_task = asyncio.create_task(worker.run(), name="sms-message-worker")

        try:
            yield
        finally:
            if worker_task is not None:
                await worker.stop()
                await worker_task
            if http_client is not None:
                await http_client.aclose()
            await store.close()

    app = FastAPI(title="SMS Android-to-Mac Bridge", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.start_worker = start_worker
    app.include_router(health_router)
    app.include_router(webhook_router)
    if resolved_settings.app_env in {"development", "test"}:
        app.include_router(development_router)
    return app


app = create_app()
