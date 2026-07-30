import time as ttime

import httpx
import pytest

from satellite.client.client import SyncClient
from satellite.models import ManagerStatus


@pytest.fixture
def python_client(monkeypatch, data_path):
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = SyncClient("http://test", transport=httpx.ASGITransport(app=app))
    yield _client


def test_ping(python_client: SyncClient):
    response = python_client.ping()
    assert response["message"] == "pong"


async def test_ping_from_async_context(python_client: SyncClient):
    response = python_client.ping()
    assert response["message"] == "pong"


def test_status(python_client: SyncClient):
    response = python_client.status()
    assert isinstance(response, ManagerStatus)
    assert response.manager_state == "idle"


def test_environment_open_close(python_client: SyncClient):
    response = python_client.environment_open()
    assert response.success, response.msg

    _initial_time = ttime.time()
    while (python_client.status()).worker_environment_state != "idle":
        ttime.sleep(0.05)

        if ttime.time() - _initial_time >= 5.0:
            pytest.fail("Timed out waiting for the worker environment to report itself as 'idle'.")

    response = python_client.environment_close()
    assert response.success, response.msg

    _initial_time = ttime.time()
    while (python_client.status()).worker_environment_exists:
        ttime.sleep(0.05)

        if ttime.time() - _initial_time >= 5.0:
            pytest.fail("Timed out waiting for the worker environment to be closed.")


def test_environment_open_close_with_wait_condition(python_client: SyncClient):
    response = python_client.environment_open()
    assert response.success, response.msg

    python_client.wait_for_idle(timeout=5)
    status = python_client.status()
    assert status.manager_state == "idle"

    response = python_client.environment_close()
    assert response.success, response.msg

    python_client.wait_for_idle(timeout=5)
    status = python_client.status()
    assert status.manager_state == "idle"
