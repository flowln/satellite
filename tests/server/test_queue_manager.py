import asyncio
from uuid import UUID

from fakeredis import FakeServer
import httpx
import pytest
import yaml

from satellite.models import (
    ConsoleUidResponse,
    HistoryItem,
    LatestConsoleResponse,
    LockResponse,
    ManagerStatus,
    QueueItem,
    RunEngineRunsResponse,
)
from satellite.server.configuration import ManagerConfiguration
from satellite.server.persistence import RedisPersistenceBackend

from .utils import assert_response, open_environment, wait_for_idle, wait_status_change


async def wait_history_change(client: httpx.AsyncClient, remaining_items: int):
    while True:
        _status = (await client.get("/queue/status")).json()
        model = ManagerStatus.model_validate(_status)

        assert model.worker_environment_exists

        if model.items_in_history == remaining_items:
            break

        await asyncio.sleep(0.05)


class TestWithSingleEnvironment:
    @pytest.fixture(scope="class")
    @classmethod
    def client(cls, default_configuration_setup) -> httpx.AsyncClient:
        from satellite.server.main import _create_app

        app = _create_app()

        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    async def with_open_environment(cls, client: httpx.AsyncClient):
        async with open_environment(client):
            yield

    @pytest.fixture(autouse=True, scope="function")
    async def clear_queue_and_history_before_test(self, client: httpx.AsyncClient):
        await wait_status_change(client, wait_for_idle(client))

        (await client.post("/queue/queue/clear")).raise_for_status()
        (await client.post("/queue/history/clear")).raise_for_status()

    async def test_ping(self, client):
        response = await client.get("/ping")

        assert response.status_code == 200
        assert response.json() == {"message": "pong"}

    async def test_queue_add_simple(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}
        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))

        response_body = response.json()
        assert response_body["qsize"] == 1

        returned_item = QueueItem.model_validate(response_body["item"])
        assert returned_item.uid is not None

    async def test_queue_add_remove_in_batch(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"items": [item.model_dump(mode="json")] * 5}

        response = assert_response(await client.post("/queue/queue/item/add/batch", json=request_body))
        assert response.json()["qsize"] == 5

        items = response.json()["items"]
        uids = [items[1]["item_uid"], items[3]["item_uid"]]

        response = assert_response(await client.post("/queue/queue/item/remove/batch", json={"uids": uids}))
        assert response.json()["qsize"] == 3

    async def test_queue_update_item(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"items": [item.model_dump(mode="json")] * 5}

        response = assert_response(await client.post("/queue/queue/item/add/batch", json=request_body))
        assert response.json()["qsize"] == 5

        second_item = response.json()["items"][1]
        second_item["name"] = "count"
        second_item["args"] = [["rand"]]
        second_item["kwargs"] = {"num": 10}

        request_body = {"item": second_item}
        response = assert_response(await client.post("/queue/item/update", json=request_body))
        assert response.json()["qsize"] == 5

        new_second_item = response.json()["item"]
        assert new_second_item["item_uid"] == second_item["item_uid"]
        assert new_second_item["name"] == second_item["name"]
        assert new_second_item["args"] == second_item["args"]
        assert new_second_item["kwargs"] == second_item["kwargs"]

        response = assert_response(await client.get("/queue/get"))
        assert not all(_i["name"] == "simple_plan" for _i in response.json()["items"])

        new_item = QueueItem(name="simple_plan", args=["rand"])
        new_item.uid = UUID(new_second_item["item_uid"])

        request_body = {"item": new_item.model_dump(mode="json")}
        response = assert_response(await client.post("/queue/item/update?replace=true", json=request_body))
        assert response.json()["qsize"] == 5

        new_second_item = response.json()["item"]
        assert new_second_item["item_uid"] != second_item["item_uid"]
        assert new_second_item["name"] == new_item.name
        assert new_second_item["args"] == new_item.args
        assert new_second_item["kwargs"] == new_item.kwargs

        response = assert_response(await client.get("/queue/get"))
        assert all(_i["name"] == "simple_plan" for _i in response.json()["items"])

    async def test_queue_move_item(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"items": [item.model_dump(mode="json")] * 3}

        response = assert_response(await client.post("/queue/queue/item/add/batch", json=request_body))
        assert response.json()["qsize"] == 3

        item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        response = assert_response(
            await client.post("/queue/queue/item/move", params={"uid": item_uids[0], "after_uid": item_uids[2]})
        )
        assert response.json()["item"]["item_uid"] == item_uids[0]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[1], item_uids[2], item_uids[0]]

        item_uids = new_item_uids

        response = assert_response(
            await client.post("/queue/queue/item/move", params={"uid": item_uids[0], "after_uid": item_uids[1]})
        )
        assert response.json()["item"]["item_uid"] == item_uids[0]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[1], item_uids[0], item_uids[2]]

        item_uids = new_item_uids

        response = assert_response(
            await client.post("/queue/queue/item/move", params={"uid": item_uids[1], "pos_dest": 0})
        )
        assert response.json()["item"]["item_uid"] == item_uids[1]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[1], item_uids[0], item_uids[2]]

        item_uids = new_item_uids

        response = assert_response(
            await client.post("/queue/queue/item/move", params={"pos": "front", "pos_dest": "back"})
        )
        assert response.json()["item"]["item_uid"] == item_uids[0]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[1], item_uids[2], item_uids[0]]

    async def test_queue_move_items_in_batch(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"items": [item.model_dump(mode="json")] * 5}

        response = assert_response(await client.post("/queue/queue/item/add/batch", json=request_body))
        assert response.json()["qsize"] == 5

        item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        response = assert_response(
            await client.post(
                "/queue/queue/item/move/batch",
                params={"after_uid": item_uids[2]},
                json={"uids": [item_uids[0], item_uids[1]]},
            )
        )
        assert [_i["item_uid"] for _i in response.json()["items"]] == [item_uids[0], item_uids[1]]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[2], item_uids[0], item_uids[1], item_uids[3], item_uids[4]]

        item_uids = new_item_uids

        response = assert_response(
            await client.post(
                "/queue/queue/item/move/batch", params={"pos_dest": 0}, json={"uids": [item_uids[4], item_uids[3]]}
            )
        )
        assert [_i["item_uid"] for _i in response.json()["items"]] == [item_uids[3], item_uids[4]]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[3], item_uids[4], item_uids[0], item_uids[1], item_uids[2]]

        item_uids = new_item_uids

        response = assert_response(
            await client.post(
                "/queue/queue/item/move/batch",
                params={"pos_dest": "back", "reorder": True},
                json={"uids": [item_uids[1], item_uids[3], item_uids[2]]},
            )
        )
        assert [_i["item_uid"] for _i in response.json()["items"]] == [item_uids[1], item_uids[3], item_uids[2]]

        response = assert_response(await client.get("/queue/queue/get"))
        new_item_uids = [_i["item_uid"] for _i in response.json()["items"]]

        assert new_item_uids == [item_uids[0], item_uids[4], item_uids[1], item_uids[3], item_uids[2]]

    async def test_queue_add_count(self, client: httpx.AsyncClient):
        item = QueueItem(name="count", args=[["rand"]], kwargs={"num": 10})
        request_body = {"item": item.model_dump(mode="json")}

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))

        response_body = response.json()
        assert response_body["qsize"] == 1

        returned_item = QueueItem.model_validate(response_body["item"])
        assert returned_item.uid is not None

    async def test_queue_add_non_existing(self, client: httpx.AsyncClient):
        item = QueueItem(name="this_is_not_a_plan")
        request_body = {"item": item.model_dump(mode="json")}
        response = await client.post("/queue/queue/item/add", json=request_body)

        assert response.status_code == 200

        response_body = response.json()
        assert not response_body["success"], response_body["msg"]
        assert "doesn't exist" in response_body["msg"]

    async def test_queue_remove_by_uid(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

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

    async def test_queue_remove_by_position(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 1

        first_uid = QueueItem.model_validate(response_body["item"]).uid

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        response = assert_response(await client.post("/queue/queue/item/add", json=request_body))
        response_body = response.json()
        assert response_body["qsize"] == 2

        second_uid = QueueItem.model_validate(response_body["item"]).uid

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

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

    async def test_queue_run_simple(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}
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
        await wait_status_change(client, wait_for_idle(client))

    async def test_history_simple(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}
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

    async def test_queue_execute(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        old_status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        old_queue_uid = old_status.plan_queue_uid
        old_history_uid = old_status.plan_history_uid

        ret = assert_response(await client.post("/queue/item/execute", json=request_body)).json()
        assert ret["item"]["item_uid"] is not None
        assert ret["item"]["execute_method"] == "execute"

        response = await client.get("/queue/status")
        assert response.status_code == 200
        assert ManagerStatus.model_validate(response.json()).plan_queue_uid == old_queue_uid

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

    async def test_queue_run_in_sequence(self, client: httpx.AsyncClient):
        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}
        for _ in range(7):
            await client.post("/queue/queue/item/add", json=request_body)

        assert_response(await client.post("/queue/queue/start"))

        response = await client.get("/queue/status")
        assert response.status_code == 200

        status = ManagerStatus.model_validate(response.json())
        assert status.worker_environment_exists

        await wait_status_change(client, wait_history_change(client, 1))
        await wait_status_change(client, wait_history_change(client, 2))

        assert_response(await client.post("/queue/queue/stop"))

        # Run until the end of the current run
        await wait_status_change(client, wait_history_change(client, 3))

        await asyncio.sleep(0.25)  # Ensure it doesn't start a new run in the meantime
        status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        assert status.worker_environment_state == "idle"

        assert_response(await client.post("/queue/queue/start"))

        await wait_status_change(client, wait_history_change(client, 5))

        assert_response(await client.post("/queue/queue/stop"))
        assert_response(await client.post("/queue/queue/stop/cancel"))

        await wait_status_change(client, wait_history_change(client, 7))

        status = ManagerStatus.model_validate((await client.get("/queue/status")).json())
        assert status.manager_state == "idle"
        assert status.worker_environment_state == "idle"
        assert status.running_item_uid is None

    async def test_run_uids(self, client: httpx.AsyncClient):
        item = QueueItem(name="plan_with_various_runs")
        request_body = {"item": item.model_dump(mode="json")}
        await client.post("/queue/queue/item/add", json=request_body)

        _status = (await client.get("/queue/re/runs")).json()
        model = RunEngineRunsResponse.model_validate(_status)
        old_run_list_uid = model.uid

        assert_response(await client.post("/queue/queue/start"))

        response = await client.get("/queue/status")
        assert response.status_code == 200

        status = ManagerStatus.model_validate(response.json())
        assert status.worker_environment_exists

        _current_run_list_uid = None

        async def wait_run_list_change(option: str, number_of_elements: int):
            nonlocal _current_run_list_uid
            while True:
                _status = (await client.get("/queue/re/runs", params={"option": option})).json()
                _model = RunEngineRunsResponse.model_validate(_status)

                if str(_model.uid) == str(old_run_list_uid):
                    await asyncio.sleep(0.05)

                    continue

                assert len(_model.runs) == number_of_elements, _model
                _current_run_list_uid = _model.uid

                break

        await wait_status_change(client, wait_run_list_change("active", 1))
        await wait_status_change(client, wait_run_list_change("open", 1))
        await wait_status_change(client, wait_run_list_change("closed", 0))

        old_run_list_uid = _current_run_list_uid

        await wait_status_change(client, wait_run_list_change("active", 1))
        await wait_status_change(client, wait_run_list_change("open", 0))
        await wait_status_change(client, wait_run_list_change("closed", 1))

        old_run_list_uid = _current_run_list_uid

        await wait_status_change(client, wait_run_list_change("active", 2))
        await wait_status_change(client, wait_run_list_change("open", 1))
        await wait_status_change(client, wait_run_list_change("closed", 1))

        old_run_list_uid = _current_run_list_uid

        await wait_status_change(client, wait_run_list_change("active", 2))
        await wait_status_change(client, wait_run_list_change("open", 0))
        await wait_status_change(client, wait_run_list_change("closed", 2))

    async def test_lock_key_for_environment(self, client: httpx.AsyncClient):
        response = assert_response(
            await client.post(
                "/lock", params={"lock_key": "1234", "environment": True, "note": "locked for testing reasons"}
            )
        )
        parsed_response = LockResponse.model_validate(response.json())
        assert parsed_response.lock_info.is_environment_locked
        assert not parsed_response.lock_info.is_queue_locked

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        # Only locked environment, not queue
        response = await client.post("/queue/item/add", json=request_body)
        assert response.status_code == 200

        response = await client.post("/queue/start")
        assert response.status_code == 423

        response = await client.post("/queue/start", params={"lock_key": "1234"})
        assert response.status_code == 200

        await wait_status_change(client, wait_history_change(client, 1))

        response = assert_response(await client.post("/unlock", params={"lock_key": "1234"}))
        parsed_response = LockResponse.model_validate(response.json())
        assert not parsed_response.lock_info.is_environment_locked
        assert not parsed_response.lock_info.is_queue_locked

        response = await client.post("/queue/item/add", json=request_body, params={"lock_key": "1234"})
        assert response.status_code == 200

        response = await client.post("/queue/start")
        assert response.status_code == 200

        await wait_status_change(client, wait_history_change(client, 2))

    async def test_lock_key_for_queue(self, client: httpx.AsyncClient):
        response = assert_response(
            await client.post("/lock", params={"lock_key": "1234", "queue": True, "note": "locked for testing reasons"})
        )
        parsed_response = LockResponse.model_validate(response.json())
        assert not parsed_response.lock_info.is_environment_locked
        assert parsed_response.lock_info.is_queue_locked

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        response = await client.post("/queue/item/add", json=request_body, params={"lock_key": "1234"})
        assert response.status_code == 200

        response = await client.post("/queue/item/add", json=request_body)
        assert response.status_code == 423

        # Only locked queue, not environment
        response = await client.post("/queue/start")
        assert response.status_code == 200

        await wait_status_change(client, wait_history_change(client, 1))

        response = assert_response(await client.post("/unlock", params={"lock_key": "1234"}))
        parsed_response = LockResponse.model_validate(response.json())
        assert not parsed_response.lock_info.is_environment_locked
        assert not parsed_response.lock_info.is_queue_locked

        response = await client.post("/queue/item/add", json=request_body)
        assert response.status_code == 200

    async def test_emergency_lock_key(self, client: httpx.AsyncClient):
        response = assert_response(
            await client.post(
                "/lock",
                params={"lock_key": "1234", "environment": True, "queue": True, "note": "locked for testing reasons"},
            )
        )
        parsed_response = LockResponse.model_validate(response.json())
        assert parsed_response.lock_info.is_environment_locked
        assert parsed_response.lock_info.is_queue_locked

        item = QueueItem(name="simple_plan", args=["rand"])
        request_body = {"item": item.model_dump(mode="json")}

        response = await client.post("/queue/item/add", json=request_body, params={"lock_key": "1234"})
        assert response.status_code == 200

        response = await client.post("/queue/item/add", json=request_body)
        assert response.status_code == 423

        response = await client.post("/queue/start", params={"lock_key": "emergency_test_key"})
        assert response.status_code == 200

        await wait_status_change(client, wait_history_change(client, 1))

        response = assert_response(await client.post("/unlock", params={"lock_key": "emergency_test_key"}))
        parsed_response = LockResponse.model_validate(response.json())
        assert not parsed_response.lock_info.is_environment_locked
        assert not parsed_response.lock_info.is_queue_locked

        response = await client.post("/queue/item/add", json=request_body)
        assert response.status_code == 200


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


async def test_open_environment_twice(client):
    async with open_environment(client):
        pass

    async with open_environment(client):
        pass


async def test_open_environment_with_error(tmp_path, monkeypatch):
    configuration = ManagerConfiguration.test_configuration()

    # Non-existent startup folder
    configuration.startup.startup_directory = tmp_path / "startup"

    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as _file:
        yaml.safe_dump(configuration.model_dump(mode="json"), stream=_file)

    monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

    from satellite.server.main import _create_app

    app = _create_app()

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    response = await client.post("/environment/open")
    response.raise_for_status()

    response_body = response.json()
    assert not response_body["success"], response_body

    response = await client.get("/status")
    response.raise_for_status()

    response_body = response.json()
    assert response_body["manager_state"] == "idle", response_body
    assert not response_body["worker_environment_exists"], response_body
