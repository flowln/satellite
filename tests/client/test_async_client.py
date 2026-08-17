import asyncio
import logging
import os
import time as ttime
from typing import cast

import httpx
import pytest
import yaml

from satellite.client.client import AsyncClient, OAuthAuthentication
from satellite.models import ManagerStatus, QueueItem
from satellite.server.configuration import ManagerConfiguration, _ManagerAuthenticationProvider
from satellite.server.security.access_policies import BasicAPIAccessPolicy


@pytest.fixture(autouse=True, scope="session")
def configure_client_logging():
    logger = logging.getLogger("satellite.client")
    logger.setLevel(logging.DEBUG)

    yield


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


class TestWithSingleEnvironment:
    @pytest.fixture(scope="class")
    @classmethod
    async def python_client(cls, data_path):
        config_path = str(data_path / "startup" / "config.yaml")
        os.environ["QSERVER_CONFIG"] = config_path

        from satellite.server.main import _create_app

        app = _create_app()

        _client = AsyncClient("http://test", transport=httpx.ASGITransport(app=app))
        yield _client

    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    async def with_environment_open(cls, python_client: AsyncClient):
        await python_client.wait_for_idle(timeout=5)
        status = await python_client.status()
        assert status.manager_state == "idle"

        response = await python_client.environment_open()
        assert response.success, response.msg

        await python_client.wait_for_idle(timeout=5)
        status = await python_client.status()
        assert status.manager_state == "idle"

        yield

        response = await python_client.environment_close()
        assert response.success, response.msg

    @pytest.fixture(autouse=True, scope="function")
    async def with_queue_and_history_clean(self, python_client: AsyncClient):
        response = await python_client.queue_clear()
        assert response.success, response.msg

        response = await python_client.history_clear()
        assert response.success, response.msg

        yield

    async def test_queue_item_add_and_run(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        response = await python_client.queue_item_add(item)
        assert response.success, response.msg

        response = await python_client.queue_start()
        assert response.success, response.msg

        await python_client.wait_for_condition(lambda s: s.worker_environment_state == "running")
        await python_client.wait_for_idle()

        history = await python_client.history_get()
        assert len(history.items) == 1
        assert history.items[0].name == "simple_plan"

    async def test_queue_item_execute(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        response = await python_client.queue_item_execute(item)
        assert response.success, response.msg
        assert response.item is not None
        assert response.item.execute_method == "execute"

        await python_client.wait_for_condition(lambda s: s.worker_environment_state == "running")
        await python_client.wait_for_idle()

        history = await python_client.history_get()
        assert len(history.items) == 1
        assert history.items[0].uid == response.item.uid
        assert history.items[0].name == "simple_plan"

    async def test_queue_add_remove_batch(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])

        response = await python_client.queue_item_add_batch([item] * 5)
        assert response.success, response.msg
        assert response.queue_size == 5
        assert response.items is not None and len(response.items) == 5

        items = response.items
        assert items[1].uid is not None
        assert items[3].uid is not None

        uids = [items[1].uid, items[3].uid]

        response = await python_client.queue_item_remove_batch(uids)
        assert response.success, response.msg
        assert response.queue_size == 3
        assert response.items is not None and len(response.items) == 2

        assert [_i.uid for _i in response.items] == uids

    async def test_queue_item_update(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])

        response = await python_client.queue_item_add_batch([item] * 5)
        assert response.success, response.msg
        assert response.queue_size == 5
        assert response.items is not None and len(response.items) == 5

        second_item = response.items[1]
        second_item.name = "count"
        second_item.args = [["rand"]]
        second_item.kwargs = {"num": 10}

        response = await python_client.queue_item_update(second_item)
        assert response.success, response.msg
        assert response.queue_size == 5

        new_second_item = response.item
        assert new_second_item is not None
        assert new_second_item.uid == second_item.uid
        assert new_second_item.name == second_item.name
        assert new_second_item.args == second_item.args
        assert new_second_item.kwargs == second_item.kwargs

        response = await python_client.queue_get()
        assert not all(_i.name == "simple_plan" for _i in response.items)

        new_item = QueueItem(name="simple_plan", args=["rand"])
        new_item.uid = new_second_item.uid

        response = await python_client.queue_item_update(new_item, replace=True)
        assert response.success, response.msg
        assert response.queue_size == 5

        new_second_item = response.item
        assert new_second_item is not None
        assert new_second_item.uid != second_item.uid
        assert new_second_item.name == new_item.name
        assert new_second_item.args == new_item.args
        assert new_second_item.kwargs == new_item.kwargs

        response = await python_client.queue_get()
        assert all(_i.name == "simple_plan" for _i in response.items)

    async def test_queue_item_move(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        response = await python_client.queue_item_add_batch([item] * 3)
        assert response.success, response.msg

        assert response.items is not None
        item_uids = [_i.uid for _i in response.items]

        # Move first to next after last
        response = await python_client.queue_item_move(pos=0, after_uid=str(item_uids[2]))
        assert response.success, response.msg

        assert response.item is not None and response.item.uid == item_uids[0]

        response = await python_client.queue_get()
        assert response.success, response.msg

        new_item_uids = [_i.uid for _i in response.items]

        assert new_item_uids == [item_uids[1], item_uids[2], item_uids[0]]

        item_uids = new_item_uids

        # Move first to second
        response = await python_client.queue_item_move(pos=0, after_uid=str(item_uids[1]))
        assert response.success, response.msg

        assert response.item is not None and response.item.uid == item_uids[0]

        response = await python_client.queue_get()
        assert response.success, response.msg

        new_item_uids = [_i.uid for _i in response.items]

        assert new_item_uids == [item_uids[1], item_uids[0], item_uids[2]]

        item_uids = new_item_uids

        # Move item in the middle to first
        response = await python_client.queue_item_move(uid=item_uids[1], pos_dest=0)
        assert response.success, response.msg

        assert response.item is not None and response.item.uid == item_uids[1]

        response = await python_client.queue_get()
        assert response.success, response.msg

        new_item_uids = [_i.uid for _i in response.items]

        assert new_item_uids == [item_uids[1], item_uids[0], item_uids[2]]

        item_uids = new_item_uids

        # Move front to back
        response = await python_client.queue_item_move(pos="front", pos_dest="back")
        assert response.success, response.msg

        assert response.item is not None and response.item.uid == item_uids[0]

        response = await python_client.queue_get()
        assert response.success, response.msg

        new_item_uids = [_i.uid for _i in response.items]

        assert new_item_uids == [item_uids[1], item_uids[2], item_uids[0]]

    async def test_queue_item_move_in_batch(self, python_client: AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        response = await python_client.queue_item_add_batch([item] * 5)
        assert response.success, response.msg

        if True:
            assert response.items is not None
            item_uids = [_i.uid for _i in response.items if _i.uid is not None]

            response = await python_client.queue_item_move_batch(
                [item_uids[0], item_uids[1]], after_uid=str(item_uids[2])
            )
            assert response.success, response.msg

            assert response.items is not None and [_i.uid for _i in response.items] == [item_uids[0], item_uids[1]]

            response = await python_client.queue_get()
            assert response.success, response.msg

            new_item_uids = [_i.uid for _i in response.items if _i.uid is not None]

            assert new_item_uids == [item_uids[2], item_uids[0], item_uids[1], item_uids[3], item_uids[4]]

            item_uids = new_item_uids

        if True:
            response = await python_client.queue_item_move_batch([item_uids[1], item_uids[3], item_uids[2]], pos_dest=0)
            assert response.success, response.msg

            assert response.items is not None and [_i.uid for _i in response.items] == [
                item_uids[1],
                item_uids[2],
                item_uids[3],
            ]

            response = await python_client.queue_get()
            assert response.success, response.msg

            new_item_uids = [_i.uid for _i in response.items if _i.uid is not None]

            assert new_item_uids == [item_uids[1], item_uids[2], item_uids[3], item_uids[0], item_uids[4]]

            item_uids = new_item_uids

        if True:
            response = await python_client.queue_item_move_batch(
                [item_uids[4], item_uids[3]], pos_dest="back", reorder=True
            )
            assert response.success, response.msg

            assert response.items is not None and [_i.uid for _i in response.items] == [item_uids[4], item_uids[3]]

            response = await python_client.queue_get()
            assert response.success, response.msg

            new_item_uids = [_i.uid for _i in response.items if _i.uid is not None]

            assert new_item_uids == [item_uids[0], item_uids[1], item_uids[2], item_uids[4], item_uids[3]]

            item_uids = new_item_uids

        if True:
            response = await python_client.queue_item_move_batch([item_uids[2]], before_uid=str(item_uids[2]))
            assert response.success, response.msg

            assert response.items is not None and [_i.uid for _i in response.items] == [item_uids[2]]

            response = await python_client.queue_get()
            assert response.success, response.msg

            new_item_uids = [_i.uid for _i in response.items if _i.uid is not None]

            assert new_item_uids == item_uids

    async def test_lock_unlock_with_property(self, python_client: AsyncClient):
        python_client.lock_key = "12345"

        response = await python_client.lock(environment=True, queue=True)
        assert response.success, response.msg
        assert response.lock_info.is_environment_locked
        assert response.lock_info.is_queue_locked

        # Should add the lock key automatically to requests.
        item = QueueItem(name="simple_plan", args=["rand"])
        response = await python_client.queue_item_add(item)
        assert response.success, response.msg

        python_client.lock_key = None

        # Should error out since the lock_key property is no longer set
        with pytest.raises(RuntimeError):
            await python_client.unlock()

        response = await python_client.unlock(lock_key="12345")
        assert response.success, response.msg
        assert not response.lock_info.is_environment_locked
        assert not response.lock_info.is_queue_locked


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
    configuration.authorization.api_access_authorization.args = {
        "roles": BasicAPIAccessPolicy.get_roles_for_admin_power("ed", "molly")
    }

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
