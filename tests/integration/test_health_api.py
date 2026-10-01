import httpx

from sms_bridge.config import Settings
from sms_bridge.main import create_app


async def test_health_returns_pass() -> None:
    app = create_app(Settings(_env_file=None), start_worker=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "pass"}


async def test_ready_reports_not_initialized_before_runtime_wiring() -> None:
    app = create_app(Settings(_env_file=None), start_worker=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_initialized"}


async def test_app_has_no_dev_simulator_in_production() -> None:
    settings = Settings(_env_file=None, app_env="production")
    app = create_app(settings, start_worker=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/dev/simulate-inbound", json={})

    assert response.status_code == 404
