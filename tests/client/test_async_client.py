import httpx
import pytest

from satellite.client.client import AsyncClient
from satellite.models import ManagerStatus


@pytest.fixture
async def python_client(monkeypatch, data_path):
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=httpx.ASGITransport(app=app))
    yield _client


async def test_ping(python_client: AsyncClient):
    response = await python_client.ping()
    assert response["message"] == "pong"


async def test_status(python_client: AsyncClient):
    response = await python_client.status()
    assert isinstance(response, ManagerStatus)
    assert response.manager_state == "idle"
