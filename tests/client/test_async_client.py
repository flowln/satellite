import asyncio
import time as ttime
from typing import cast

import httpx
import pytest
import yaml

from satellite.client.client import AsyncClient, OAuthAuthentication
from satellite.models import ManagerStatus
from satellite.server.configuration import ManagerConfiguration, _ManagerAuthenticationProvider


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


@pytest.fixture
async def python_client_with_auth(monkeypatch, tmp_path):
    configuration = ManagerConfiguration()
    configuration.network.use_mocked_backend = True
    provider = _ManagerAuthenticationProvider(
        provider="test",
        expiration_time=10.0,
        authenticator="satellite.server.security.authenticators:DictionaryAuthenticator",
        args={"users_to_passwords": {"ed": "123", "molly": "456"}},
    )
    configuration.authentication.providers = [provider]

    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as _file:
        yaml.safe_dump(configuration.model_dump(), stream=_file)

    monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=httpx.ASGITransport(app=app))
    yield _client


async def test_without_login(python_client_with_auth: AsyncClient):
    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        await python_client_with_auth.status()


async def test_with_login(python_client_with_auth: AsyncClient):
    await python_client_with_auth.login("ed", "123")

    response = await python_client_with_auth.status()
    assert isinstance(response, ManagerStatus)
    assert response.manager_state == "idle"


async def test_with_login_transparent_refresh(python_client_with_auth: AsyncClient):
    tokens = await python_client_with_auth.login("ed", "123", expiration_time=1.0)
    old_access_token = tokens.token

    await python_client_with_auth.status()
    await asyncio.sleep(1.0)
    await python_client_with_auth.status()

    new_access_token = cast(OAuthAuthentication, python_client_with_auth.auth).access_token  # noqa

    assert old_access_token != new_access_token


async def test_with_login_logout(python_client_with_auth: AsyncClient):
    await python_client_with_auth.login("ed", "123")

    await python_client_with_auth.status()

    await python_client_with_auth.logout()

    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        await python_client_with_auth.status()


async def test_with_login_refresh(python_client_with_auth: AsyncClient):
    await python_client_with_auth.login("ed", "123", expiration_time=1.0)
    await python_client_with_auth.status()
    await asyncio.sleep(1.0)
    await python_client_with_auth.refresh_session(expiration_time=1.0)
    await python_client_with_auth.status()


async def test_with_login_whoami(python_client_with_auth: AsyncClient):
    await python_client_with_auth.login("ed", "123")

    information = await python_client_with_auth.whoami()
    assert information.user_name == "ed"
