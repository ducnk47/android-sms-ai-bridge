from fastapi import FastAPI

from sms_bridge.api.health import router as health_router
from sms_bridge.config import Settings
from sms_bridge.logging import configure_logging


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(include_message_bodies=resolved_settings.log_message_bodies)

    app = FastAPI(title="SMS Android-to-Mac Bridge")
    app.state.settings = resolved_settings
    app.state.start_worker = start_worker
    app.include_router(health_router)
    return app


app = create_app()
