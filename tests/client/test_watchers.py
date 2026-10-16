from collections.abc import AsyncGenerator, Callable
import logging
import time as ttime
import weakref

import httpx
import pytest

from satellite.client.client import AsyncClient
from satellite.client.watchers import ConsoleWatcher, StatusWatcher, WatchMethod
from satellite.server.console import get_global_log_centralizer

from .asgi_transport_with_streaming import ASGIStreamingTransport


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
    _watcher = StatusWatcher(weakref.ref(_client), WatchMethod.POLLING, polling_time=0.1)

    _watcher.start()
    yield _client, _watcher
    _watcher.stop(timeout=0.25)


@pytest.fixture
async def sse_status_watcher(monkeypatch, data_path) -> AsyncGenerator[tuple[AsyncClient, StatusWatcher]]:
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=ASGIStreamingTransport(app=app))
    _watcher = StatusWatcher(weakref.ref(_client), WatchMethod.SERVER_SENT_EVENTS)

    _watcher.start()
    yield _client, _watcher
    _watcher.stop(timeout=0.25)


@pytest.fixture(params=[polling_status_watcher, sse_status_watcher])
def status_watcher(request):
    yield request.getfixturevalue(request.param.__name__)


async def test_status_watcher(status_watcher: tuple[AsyncClient, StatusWatcher]):
    client, watcher = status_watcher

    received_status = []

    def on_new_status(new_status):
        nonlocal received_status

        received_status.append(new_status)

    watcher.add_callback(on_new_status, call_now=True)

    with_timeout(lambda: len(received_status) == 1, timeout=2.5)

    current_status = await client.status()
    assert str(current_status.status_uid) == received_status[0]["status_uid"]

    response = await client.post("/status/update_uid")
    assert response.status_code == 200

    with_timeout(lambda: len(received_status) == 2, timeout=2.5)

    current_status = await client.status()
    assert str(current_status.status_uid) != received_status[0]["status_uid"]
    assert str(current_status.status_uid) == received_status[1]["status_uid"]


@pytest.fixture
async def polling_console_watcher(monkeypatch, data_path) -> AsyncGenerator[tuple[AsyncClient, ConsoleWatcher]]:
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=httpx.ASGITransport(app=app))
    _watcher = ConsoleWatcher(weakref.ref(_client), WatchMethod.POLLING, polling_time=0.1)

    _watcher.start()
    yield _client, _watcher
    _watcher.stop(timeout=0.25)


@pytest.fixture
async def sse_console_watcher(monkeypatch, data_path) -> AsyncGenerator[tuple[AsyncClient, ConsoleWatcher]]:
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    from satellite.server.main import _create_app

    app = _create_app()

    _client = AsyncClient("http://test", transport=ASGIStreamingTransport(app=app))
    _watcher = ConsoleWatcher(weakref.ref(_client), WatchMethod.SERVER_SENT_EVENTS)

    _watcher.start()
    yield _client, _watcher
    _watcher.stop(timeout=0.25)


@pytest.fixture(params=[polling_console_watcher, sse_console_watcher])
def console_watcher(request):
    yield request.getfixturevalue(request.param.__name__)


async def test_console_watcher(console_watcher: tuple[AsyncClient, ConsoleWatcher]):
    client, watcher = console_watcher

    received_console_output = []

    def on_new_console(new_console):
        nonlocal received_console_output

        received_console_output.append(new_console)

    watcher.add_callback(on_new_console, call_now=True)

    with_timeout(lambda: len(received_console_output) == 1, timeout=2.5)

    current_console_uid = await client.console_output_uid()
    assert str(current_console_uid.uid) == received_console_output[0]["last_msg_uid"]

    log_centralizer = get_global_log_centralizer()
    logger = logging.getLogger("satellite.test")
    log_centralizer.add_handler_to_logger("queue", logger)

    logger.info("New console line!")

    with_timeout(lambda: len(received_console_output) == 2, timeout=2.5)

    current_console_uid = await client.console_output_uid()
    assert str(current_console_uid.uid) != received_console_output[0]["last_msg_uid"]
    assert str(current_console_uid.uid) == received_console_output[1]["last_msg_uid"]
