import asyncio
import time as ttime

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


async def test_environment_open_close(python_client: AsyncClient):
    response = await python_client.environment_open()
    assert response.success, response.msg

    _initial_time = ttime.time()
    while (await python_client.status()).worker_environment_state != "idle":
        await asyncio.sleep(0.05)

        if ttime.time() - _initial_time >= 5.0:
            pytest.fail("Timed out waiting for the worker environment to report itself as 'idle'.")

    response = await python_client.environment_close()
    assert response.success, response.msg

    _initial_time = ttime.time()
    while (await python_client.status()).worker_environment_exists:
        await asyncio.sleep(0.05)

        if ttime.time() - _initial_time >= 5.0:
            pytest.fail("Timed out waiting for the worker environment to be closed.")


async def test_environment_open_close_with_wait_condition(python_client: AsyncClient):
    response = await python_client.environment_open()
    assert response.success, response.msg

    await python_client.wait_for_idle(timeout=5)
    status = await python_client.status()
    assert status.manager_state == "idle"

    response = await python_client.environment_close()
    assert response.success, response.msg

    await python_client.wait_for_idle(timeout=5)
    status = await python_client.status()
    assert status.manager_state == "idle"
