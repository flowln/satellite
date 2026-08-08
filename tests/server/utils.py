import asyncio
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from pprint import pprint

import httpx

from satellite.models import ManagerStatus


async def wait_status_change(client: httpx.AsyncClient, coro: Coroutine, *, timeout: float = 5.0):
    try:
        async with asyncio.timeout(timeout):
            await coro
    except TimeoutError:
        _status = await client.get("/queue/status")
        pprint(_status.json())

        raise


@asynccontextmanager
async def open_environment(client: httpx.AsyncClient, *, clear: bool = True):
    async def _wait_for_idle(should_environment_be_alive: bool = False):
        ready = False
        while not ready:
            _status = (await client.get("/queue/status")).json()
            model = ManagerStatus.model_validate(_status)
            ready = model.manager_state == "idle"

            if should_environment_be_alive:
                ready &= model.worker_environment_exists

            await asyncio.sleep(0.01)

    await wait_status_change(client, _wait_for_idle())

    if clear:
        (await client.post("/queue/queue/clear")).raise_for_status()
        (await client.post("/queue/history/clear")).raise_for_status()

    (await client.post("/queue/environment/open")).raise_for_status()

    await wait_status_change(client, _wait_for_idle(should_environment_be_alive=True))

    yield

    (await client.post("/queue/environment/close")).raise_for_status()

    await wait_status_change(client, _wait_for_idle())


def assert_response(response: httpx.Response) -> httpx.Response:
    assert response.status_code == 200, response.json()

    response_body = response.json()
    assert response_body["success"], response_body["msg"]
    assert response_body["msg"] == ""

    return response
