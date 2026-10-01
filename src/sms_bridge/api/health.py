from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["pass"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="pass")


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    store = getattr(request.app.state, "store", None)
    gateway = getattr(request.app.state, "gateway", None)
    if store is None or gateway is None:
        return JSONResponse(status_code=503, content={"status": "not_initialized"})

    try:
        database_healthy = await store.healthcheck()
    except Exception:
        database_healthy = False
    try:
        gateway_healthy = await gateway.health()
    except Exception:
        gateway_healthy = False
    ready_status = database_healthy and gateway_healthy
    return JSONResponse(
        status_code=200 if ready_status else 503,
        content={
            "status": "pass" if ready_status else "fail",
            "components": {
                "database": "pass" if database_healthy else "fail",
                "gateway": "pass" if gateway_healthy else "fail",
            },
        },
    )
