import asyncio
from uuid import UUID

import httpx
import pytest

from satellite.models import HistoryItem, HistoryResponse, ManagerStatus, QueueItem

from .utils import assert_response, open_environment, wait_status_change


async def wait_for_idle(client: httpx.AsyncClient):
    async def _wait_for_idle():
        ready = False
        while not ready:
            _status = (await client.get("/queue/status")).json()
            ready = ManagerStatus.model_validate(_status).manager_state == "idle"

            await asyncio.sleep(0.01)

    await wait_status_change(client, _wait_for_idle())


async def wait_for_queue_start(client: httpx.AsyncClient, old_queue_uid: UUID, old_queue_size: int):
    response = await client.get("/queue/status")
    assert response.status_code == 200

    status = ManagerStatus.model_validate(response.json())
    assert status.worker_environment_exists

    async def wait_queue_change():
        while True:
            _status = (await client.get("/queue/status")).json()
            model = ManagerStatus.model_validate(_status)

            assert model.worker_environment_exists

            if model.plan_queue_uid != old_queue_uid and model.items_in_queue == old_queue_size - 1:
                break

            await asyncio.sleep(0.05)

    await wait_status_change(client, wait_queue_change())


async def wait_for_run_engine_paused(client: httpx.AsyncClient, *, timeout: float = 5.0):
    response = await client.get("/queue/status")
    assert response.status_code == 200

    status = ManagerStatus.model_validate(response.json())
    assert status.worker_environment_exists

    async def wait_queue_change():
        while True:
            _status = (await client.get("/queue/status")).json()
            model = ManagerStatus.model_validate(_status)

            assert model.worker_environment_exists

            if model.worker_environment_state == "paused":
                break

            await asyncio.sleep(0.05)

    await wait_status_change(client, wait_queue_change(), timeout=timeout)


async def wait_for_run_engine_running(client: httpx.AsyncClient):
    response = await client.get("/queue/status")
    assert response.status_code == 200

    status = ManagerStatus.model_validate(response.json())
    assert status.worker_environment_exists

    async def wait_queue_change():
        while True:
            _status = (await client.get("/queue/status")).json()
            model = ManagerStatus.model_validate(_status)

            assert model.worker_environment_exists

            if model.worker_environment_state == "running":
                break

            await asyncio.sleep(0.05)

    await wait_status_change(client, wait_queue_change())


async def wait_until_item_ran(client: httpx.AsyncClient, old_history_uid: UUID, old_history_size: int):
    async def wait_history_change():
        while True:
            _status = await client.get("/queue/status")
            model = ManagerStatus.model_validate(_status.json())
            if model.plan_history_uid != old_history_uid and model.items_in_history == old_history_size + 1:
                break

            await asyncio.sleep(0.05)

    await wait_status_change(client, wait_history_change())


class TestPlanExecution:
    @pytest.fixture(autouse=True)
    async def with_environment_open(self, client: httpx.AsyncClient):
        (await client.post("/queue/queue/clear")).raise_for_status()
        (await client.post("/queue/history/clear")).raise_for_status()

        (await client.post("/queue/environment/open")).raise_for_status()

        await wait_for_idle(client)

        yield

        (await client.post("/queue/environment/close")).raise_for_status()

        await wait_for_idle(client)

    async def test_failing_plan(self, client: httpx.AsyncClient):
        item = QueueItem(name="failing_plan")
        request_body = item.model_dump(mode="json")
        await client.post("/queue/queue/item/add", json=request_body)

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid
        old_queue_size = old_status.items_in_queue
        old_history_uid = old_status.plan_history_uid
        old_history_size = old_status.items_in_history

        assert_response(await client.post("/queue/queue/start"))

        await wait_for_queue_start(client, old_queue_uid, old_queue_size)
        await wait_until_item_ran(client, old_history_uid, old_history_size)

        response = await client.get("/queue/history/get")
        assert response.status_code == 200

        history = HistoryItem.model_validate(response.json()["items"][0])
        assert history.name == item.name
        assert history.exit_status == "failed"

        assert history.msg == "This test always fails"
        assert "RuntimeError" in history.traceback

    @pytest.mark.parametrize(
        ("route_name", "exit_status"),
        (("re/stop", "stopped"), ("re/abort", "aborted"), ("re/halt", "halted")),
    )
    async def test_good_stuck_plan(self, client: httpx.AsyncClient, route_name: str, exit_status: str):
        item = QueueItem(name="good_stuck_plan")
        request_body = item.model_dump(mode="json")
        await client.post("/queue/queue/item/add", json=request_body)

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid
        old_queue_size = old_status.items_in_queue
        old_history_uid = old_status.plan_history_uid
        old_history_size = old_status.items_in_history

        assert_response(await client.post("/queue/queue/start"))

        await wait_for_queue_start(client, old_queue_uid, old_queue_size)
        await client.post("/queue/re/pause?option=immediate")
        await wait_for_run_engine_paused(client)
        await client.post(f"/queue/{route_name}")
        await wait_until_item_ran(client, old_history_uid, old_history_size)

        response = await client.get("/queue/history/get")
        assert response.status_code == 200

        history = HistoryItem.model_validate(response.json()["items"][0])
        assert history.name == item.name
        assert history.exit_status == exit_status

    async def test_bad_stuck_plan(self, client: httpx.AsyncClient):
        item = QueueItem(name="bad_stuck_plan")
        request_body = item.model_dump(mode="json")
        await client.post("/queue/queue/item/add", json=request_body)

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid
        old_queue_size = old_status.items_in_queue

        assert_response(await client.post("/queue/queue/start"))

        await wait_for_queue_start(client, old_queue_uid, old_queue_size)

        # The plan doesn't allow the asyncio event loop to run, so this shouldn't work.
        await client.post("/queue/re/pause?option=immediate")

        with pytest.raises(TimeoutError):
            await wait_for_run_engine_paused(client, timeout=3.0)

        await client.post("/queue/environment/destroy")

        await wait_for_idle(client)


async def test_queue_run_instruction(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")
        for _ in range(3):
            assert_response(await client.post("/queue/queue/item/add", json=request_body))

        instruction_item = QueueItem(name="queue_stop", item_type="instruction")
        instruction_item_request = instruction_item.model_dump(mode="json")
        assert_response(await client.post("/queue/queue/item/add", json=instruction_item_request))

        for _ in range(3):
            assert_response(await client.post("/queue/queue/item/add", json=request_body))

        assert_response(await client.post("/queue/queue/start"))

        response = await client.get("/queue/status")
        assert response.status_code == 200

        status = ManagerStatus.model_validate(response.json())
        assert status.worker_environment_exists

        async def wait_history_change(remaining_items: int):
            while True:
                _status = (await client.get("/queue/status")).json()
                model = ManagerStatus.model_validate(_status)

                assert model.worker_environment_exists

                if model.items_in_history == remaining_items:
                    break

                await asyncio.sleep(0.05)

        await wait_status_change(client, wait_history_change(4))

        status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        assert status.manager_state == "idle"
        assert status.worker_environment_state == "idle"
        assert status.running_item_uid is None

        last_history = HistoryResponse.model_validate((await client.get("/queue/history/get?limit=1")).json())
        instruction_item = last_history.items[0]
        assert instruction_item.type == "instruction"
        assert instruction_item.name == "queue_stop"

        assert_response(await client.post("/queue/queue/start"))

        await wait_status_change(client, wait_history_change(7))
