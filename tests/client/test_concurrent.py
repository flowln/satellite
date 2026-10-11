from collections.abc import AsyncGenerator, Callable
import logging
import time as ttime
import weakref

import httpx
import pytest

from satellite.client.client import AsyncClient
from satellite.client.concurrent import StatusWatcher, WatchMethod


def with_timeout(func: Callable[..., bool], timeout: float = 1.0, poll_time: float = 0.01):
    _initial_time = ttime.time()

    while not func():
        assert ttime.time() - _initial_time < timeout

        ttime.sleep(poll_time)


@pytest.fixture(autouse=True, scope="session")
def configure_client_logging():
    logger = logging.getLogger("satellite.client")
    logger.setLevel(logging.DEBUG)

    yield


@pytest.fixture
async def polling_status_watcher(monkeypatch, data_path) -> AsyncGenerator[tuple[AsyncClient, StatusWatcher]]:
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=httpx.ASGITransport(app=app))
    _watcher = StatusWatcher(weakref.ref(_client), WatchMethod.POLLING, polling_time=0.05)

    _watcher.start()
    yield _client, _watcher
    _watcher.stop(timeout=0.25)


async def test_polling_status_watcher(polling_status_watcher: tuple[AsyncClient, StatusWatcher]):
    client, watcher = polling_status_watcher

    received_status = []

    def on_new_status(new_status):
        nonlocal received_status

        received_status.append(new_status)

    watcher.add_callback(on_new_status, call_now=False)

    assert len(received_status) == 0
    with_timeout(lambda: len(received_status) == 1)

    current_status = await client.status()
    assert str(current_status.status_uid) == received_status[0]["status_uid"]

    response = await client.post("/status/update_uid")
    assert response.status_code == 200

    with_timeout(lambda: len(received_status) == 2)

    current_status = await client.status()
    assert str(current_status.status_uid) != received_status[0]["status_uid"]
    assert str(current_status.status_uid) == received_status[1]["status_uid"]
