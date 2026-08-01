from collections.abc import Callable
import os
from pathlib import Path
import time
from typing import Self

import httpx
import pytest

from satellite.models import HistoryItem, QueueItem, create_uuid


class Status:
    def __init__(self):
        self.done: bool = False
        self.success: bool = False

    def add_callback(self, callback: Callable[[Self], None]) -> None:
        return

    def exception(self, timeout: float | None = 0.0) -> BaseException | None:
        return


@pytest.fixture
def sim_readable():
    protocols = pytest.importorskip("bluesky.protocols")

    class Readable(protocols.Readable):
        def __init__(self, name: str, value: int):
            self._name = name
            self._value = value

        def name(self) -> str:
            return self._name

        def describe(self):
            return {self._name: {"source": "sim", "dtype": "integer", "shape": []}}

        def read(self):
            return {self._name: {"value": self._value, "timestamp": time.time()}}

    return Readable("readable", value=0)


@pytest.fixture
def sim_movable():
    protocols = pytest.importorskip("bluesky.protocols")

    class Movable(protocols.Readable, protocols.Movable):
        def __init__(self, name: str, value: int | float):
            self._name = name
            self._value = value

        def name(self) -> str:
            return self._name

        def describe(self):
            return {self._name: {"source": "sim", "dtype": "number", "shape": []}}

        def read(self):
            return {self._name: {"value": self._value, "timestamp": time.time()}}

        def set(self, value: int | float):
            self._value = value

            ret = Status()
            ret.done = True
            ret.success = True

            return ret

    return Movable("movable", value=0.0)


@pytest.fixture
def sample_items(sim_readable, sim_movable) -> tuple[QueueItem, QueueItem]:
    _r_item = QueueItem(name="simple_plan", item_uid=create_uuid(), args=[sim_readable.name()])
    _m_item = QueueItem(name="simple_plan", item_uid=create_uuid(), args=[sim_movable.name()])
    return (_r_item, _m_item)


@pytest.fixture
def sample_history_items(sample_items) -> tuple[HistoryItem, HistoryItem]:
    _r_item = HistoryItem.from_queue_item(sample_items[0])
    _m_item = HistoryItem.from_queue_item(sample_items[1])
    return (_r_item, _m_item)


@pytest.fixture
def data_path() -> Path:
    return Path(__file__).parent / "testdata"


@pytest.fixture(scope="session", autouse=True)
def configure_multiprocessing_module():
    from multiprocessing import get_start_method, set_start_method
    import sys

    old_start_method = get_start_method()
    if sys.platform == "linux":
        set_start_method("forkserver", force=True)
    else:
        set_start_method("spawn", force=True)

    yield

    set_start_method(old_start_method, force=True)


@pytest.fixture(scope="session", autouse=True)
def ensure_environment_packages_are_cached():
    import subprocess

    subprocess.run(["pip", "install", "ophyd", "matplotlib"])


@pytest.fixture
def default_configuration_setup(monkeypatch, data_path):
    if os.getenv("QSERVER_CONFIG") is not None:
        return

    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)


@pytest.fixture
def client(default_configuration_setup) -> httpx.AsyncClient:
    from satellite.server.main import _create_app

    app = _create_app()

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def pytest_addoption(parser):
    parser.addoption(
        "--session",
        action="store_true",
        help="Run session-wide tests (no clean state after each test.)",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc):
    if "client" in metafunc.fixturenames:
        scope = "session" if metafunc.config.getoption("session") else "function"

        metafunc.parametrize("client", (client,), indirect=True, scope=scope, ids=(scope,))
