from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["pass"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="pass")


@router.get("/ready")
async def ready() -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "not_initialized"})

