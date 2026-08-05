import asyncio

from fakeredis import FakeServer
import httpx
import pytest

from satellite.models import (
    ConsoleUidResponse,
    HistoryItem,
    LatestConsoleResponse,
    ManagerStatus,
    QueueItem,
)
from satellite.server.persistence import RedisPersistenceBackend

from .utils import assert_response, open_environment, wait_status_change


async def test_ping(client):
    response = await client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"message": "pong"}


async def test_queue_add_simple(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")
        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))

    response_body = response.json()
    assert response_body["qsize"] == 1

    returned_item = QueueItem.model_validate(response_body["item"])
    assert returned_item.uid is not None


async def test_queue_add_count(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="count", args=[["rand"]], kwargs={"num": 10})
        request_body = item.model_dump(mode="json")

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))

    response_body = response.json()
    assert response_body["qsize"] == 1

    returned_item = QueueItem.model_validate(response_body["item"])
    assert returned_item.uid is not None


async def test_queue_add_non_existing(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="this_is_not_a_plan")
        request_body = item.model_dump(mode="json")
        response = await client.post("/queue/queue/item/add", json=request_body)

    assert response.status_code == 200

    response_body = response.json()
    assert not response_body["success"], response_body["msg"]
    assert "doesn't exist" in response_body["msg"]


async def test_queue_remove_by_uid(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 1

    added_item = QueueItem.model_validate(response_body["item"])
    assert added_item.uid is not None

    response = assert_response(await client.post(f"/queue/queue/item/remove?uid={added_item.uid}"))
    response_body = response.json()
    assert response_body["qsize"] == 0

    removed_item = QueueItem.model_validate(response_body["item"])
    assert removed_item.uid == added_item.uid


async def test_queue_remove_by_position(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 1

        first_uid = QueueItem.model_validate(response_body["item"]).uid

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 2

        second_uid = QueueItem.model_validate(response_body["item"]).uid

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 3

        third_uid = QueueItem.model_validate(response_body["item"]).uid

    response = assert_response(await client.post("/queue/queue/item/remove?pos=1"))
    response_body = response.json()
    assert response_body["qsize"] == 2

    removed_item = QueueItem.model_validate(response_body["item"])
    assert removed_item.uid == second_uid, (first_uid, second_uid, third_uid)

    response = assert_response(await client.post("/queue/queue/item/remove?pos=back"))
    response_body = response.json()
    assert response_body["qsize"] == 1

    removed_item = QueueItem.model_validate(response_body["item"])
    assert removed_item.uid == third_uid, (first_uid, second_uid, third_uid)


async def test_queue_run_simple(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")
        await client.post("/queue/queue/item/add", json=request_body)

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid

        assert_response(await client.post("/queue/queue/start"))

        response = await client.get("/queue/status")
        assert response.status_code == 200

        status = ManagerStatus.model_validate(response.json())
        assert status.worker_environment_exists

        async def wait_queue_change():
            while True:
                _status = (await client.get("/queue/status")).json()
                model = ManagerStatus.model_validate(_status)

                assert model.worker_environment_exists

                if model.plan_queue_uid != old_queue_uid and model.items_in_queue == 0:
                    break

                await asyncio.sleep(0.05)

        await wait_status_change(client, wait_queue_change())


async def test_history_simple(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")
        await client.post("/queue/queue/item/add", json=request_body)

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid
        old_history_uid = old_status.plan_history_uid

        assert_response(await client.post("/queue/queue/start"))

        response = await client.get("/queue/status")
        assert response.status_code == 200

        status = ManagerStatus.model_validate(response.json())
        assert status.worker_environment_exists

        async def wait_queue_change():
            while True:
                _status = await client.get("/queue/status")
                model = ManagerStatus.model_validate(_status.json())
                if model.plan_queue_uid != old_queue_uid and model.items_in_queue == 0:
                    break

                await asyncio.sleep(0.05)

        await wait_status_change(client, wait_queue_change())

        async def wait_history_change():
            while True:
                _status = await client.get("/queue/status")
                model = ManagerStatus.model_validate(_status.json())
                if model.plan_history_uid != old_history_uid and model.items_in_history == 1:
                    break

                await asyncio.sleep(0.05)

        await wait_status_change(client, wait_history_change())

    response = await client.get("/queue/history/get")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1, response.json()

    history = HistoryItem.model_validate(response.json()["items"][0])
    assert history.name == item.name
    assert history.exit_status == "completed"


async def test_queue_run_in_sequence(client: httpx.AsyncClient):
    async with open_environment(client):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = item.model_dump(mode="json")
        for _ in range(7):
            await client.post("/queue/queue/item/add", json=request_body)

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

        await wait_status_change(client, wait_history_change(1))
        await wait_status_change(client, wait_history_change(2))

        assert_response(await client.post("/queue/queue/stop"))

        # Run until the end of the current run
        await wait_status_change(client, wait_history_change(3))

        await asyncio.sleep(0.25)  # Ensure it doesn't start a new run in the meantime
        status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        assert status.worker_environment_state == "idle"

        assert_response(await client.post("/queue/queue/start"))

        await wait_status_change(client, wait_history_change(5))

        assert_response(await client.post("/queue/queue/stop"))
        assert_response(await client.post("/queue/queue/stop/cancel"))

        await wait_status_change(client, wait_history_change(7))

        status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        assert status.manager_state == "idle"
        assert status.worker_environment_state == "idle"
        assert status.running_item_uid is None


@pytest.fixture
async def prepopulated_client(monkeypatch, data_path, sample_items, sample_history_items) -> httpx.AsyncClient:
    config_path = str(data_path / "startup" / "config.yaml")
    monkeypatch.setenv("QSERVER_CONFIG", config_path)

    fake_server = FakeServer()
    backend = RedisPersistenceBackend(
        queue_name="queue",
        key_prefix="qs_default",
        mock=True,
        mock_fake_server=fake_server,
    )
    await backend.queue_insert_item(sample_items[0])
    await backend.history_insert_item(sample_history_items[0])
    await backend.history_insert_item(sample_history_items[1])

    from satellite.server.main import _create_app

    app = _create_app(mock_arguments={"mock_fake_server": fake_server})

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_load_state_on_startup(prepopulated_client: httpx.AsyncClient):
    response = await prepopulated_client.get("/queue/status")
    status = ManagerStatus.model_validate(response.json())

    assert status.items_in_queue == 1
    assert status.items_in_history == 2


async def test_console_output(client: httpx.AsyncClient):
    response = await client.get("/queue/console_output/uid")
    uid_obj = ConsoleUidResponse.model_validate(response.json())

    assert uid_obj.success, uid_obj

    initial_uid = uid_obj.uid

    async with open_environment(client):
        response = await client.get("/queue/console_output/uid")
        uid_obj = ConsoleUidResponse.model_validate(response.json())

        assert uid_obj.success, uid_obj

        end_uid = uid_obj.uid

        assert initial_uid != end_uid

        response = await client.get("/queue/console_output")
        console_obj = LatestConsoleResponse.model_validate(response.json())

        assert console_obj.success

        message = "Finished loading environment!"
        assert any(message in _line for _line in console_obj.lines), console_obj.lines

        message = "Server started successfully!"
        assert any(message in _line for _line in console_obj.lines), console_obj.lines


async def test_console_output_update(client: httpx.AsyncClient):
    response = await client.get("/queue/console_output/uid")
    uid_obj = ConsoleUidResponse.model_validate(response.json())

    assert uid_obj.success, uid_obj

    initial_uid = uid_obj.uid

    async with open_environment(client):
        response = await client.get("/queue/console_output/uid")
        uid_obj = ConsoleUidResponse.model_validate(response.json())

        assert uid_obj.success, uid_obj

        end_uid = uid_obj.uid

        assert initial_uid != end_uid

        response = await client.get(f"/queue/console_output_update?last_msg_uid={initial_uid}")
        console_obj = LatestConsoleResponse.model_validate(response.json())

        assert console_obj.success

        message = "Finished loading environment!"
        assert any(message in _line for _line in console_obj.lines), console_obj.lines

        message = "Server started successfully!"
        assert not any(message in _line for _line in console_obj.lines), console_obj.lines
