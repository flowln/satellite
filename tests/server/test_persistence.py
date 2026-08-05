import asyncio

from bluesky.plans import count
import pytest

from satellite.annotations import (
    DeviceAnnotation,
    PlanAnnotation,
    generate_annotation_for_device,
    generate_annotation_for_plan,
)
from satellite.models import HistoryItem, QueueItem
from satellite.server.persistence import PersistenceBackend, RedisPersistenceBackend


@pytest.fixture
def backend():
    return RedisPersistenceBackend(mock=True, queue_name="test_queue", key_prefix="prefix")


def test_key_composition(backend: PersistenceBackend):
    assert backend.full_key_prefix == "prefix:test_queue"


async def test_existing_plans(backend: PersistenceBackend):
    existing = await backend.get_existing_plans()
    assert isinstance(existing, dict)
    assert len(existing) == 0

    existing = await backend.get_existing_plans(sub_key="count")
    assert existing is None

    await backend.set_existing_plans({"count": generate_annotation_for_plan(count, "count")})

    existing = await backend.get_existing_plans()
    assert isinstance(existing, dict)
    assert len(existing) == 1

    existing = await backend.get_existing_plans(sub_key="count")
    assert isinstance(existing, PlanAnnotation)
    assert existing.plan_name == "count"


async def test_existing_devices(backend: PersistenceBackend, sim_readable):
    existing = await backend.get_existing_devices()
    assert isinstance(existing, dict)
    assert len(existing) == 0

    existing = await backend.get_existing_devices(sub_key="readable")
    assert existing is None

    await backend.set_existing_devices({"readable": generate_annotation_for_device(sim_readable, "readable")})

    existing = await backend.get_existing_devices()
    assert isinstance(existing, dict)
    assert len(existing) == 1

    existing = await backend.get_existing_devices(sub_key="readable")
    assert isinstance(existing, DeviceAnnotation)
    assert existing.device_name == "readable"


async def test_queue(backend: PersistenceBackend, sample_items: tuple[QueueItem, QueueItem]):
    # Check: queue_get on an empty queue works fine
    current = await backend.queue_get()
    assert isinstance(current, list)
    assert len(current) == 0

    # Check: queue_get accepts all valid parameters
    await asyncio.gather(
        backend.queue_get(offset=1),
        backend.queue_get(limit=1),
        backend.queue_get(limit=0),
    )

    await backend.queue_insert_item(sample_items[0])

    # Check: queue_insert_item worked
    current = await backend.queue_get()
    assert isinstance(current, list)
    assert len(current) == 1
    assert current[0].uid == sample_items[0].uid

    await backend.queue_insert_item(sample_items[1])

    # Check: queue_get, queue_insert_item inserted at the correct place
    current = await backend.queue_get()
    assert isinstance(current, list)
    assert len(current) == 2
    assert current[0].uid == sample_items[1].uid
    assert current[1].uid == sample_items[0].uid

    # Check: queue_get_item with index parameter
    idx, first = await backend.queue_get_item(index=0)
    assert idx == 0
    assert first.uid == sample_items[1].uid

    # Check: queue_get_item with uuid parameter
    idx, second = await backend.queue_get_item(uuid=sample_items[0].uid)
    assert idx == 1
    assert second.uid == sample_items[0].uid

    # Check: queue_pop_item returns the correct item
    removed = await backend.queue_pop_item()
    assert removed.uid == sample_items[1].uid

    # Check: queue_length works as intended, and queue_pop_item worked as intended
    current_length = await backend.queue_length()
    assert current_length == 1

    await backend.queue_insert_item(sample_items[1], index=None)

    # Check: queue_get, queue_insert_item inserted at the correct place with the index parameter
    current = await backend.queue_get()
    assert isinstance(current, list)
    assert len(current) == 2
    assert current[0].uid == sample_items[0].uid
    assert current[1].uid == sample_items[1].uid

    await backend.queue_clear()

    # Check: queue_length, queue_clear worked as intended
    current_length = await backend.queue_length()
    assert current_length == 0


async def test_history(
    backend: PersistenceBackend,
    sample_history_items: tuple[HistoryItem, HistoryItem],
):
    # Check: history_get on an empty history works fine
    current = await backend.history_get()
    assert isinstance(current, list)
    assert len(current) == 0

    # Check: history_get accepts all valid parameters
    await asyncio.gather(
        backend.history_get(offset=1),
        backend.history_get(limit=1),
        backend.history_get(limit=0),
    )

    await backend.history_insert_item(sample_history_items[0])

    # Check: history_insert_item worked
    current = await backend.history_get()
    assert isinstance(current, list)
    assert len(current) == 1
    assert current[0].uid == sample_history_items[0].uid

    await backend.history_insert_item(sample_history_items[1])

    # Check: history_get, history_insert_item inserted at the correct place
    current = await backend.history_get()
    assert isinstance(current, list)
    assert len(current) == 2
    assert current[0].uid == sample_history_items[1].uid
    assert current[1].uid == sample_history_items[0].uid

    # Check: history_get_item with index parameter
    idx, first = await backend.history_get_item(index=0)
    assert idx == 0
    assert first.uid == sample_history_items[1].uid

    # Check: history_get_item with uuid parameter
    idx, second = await backend.history_get_item(uuid=sample_history_items[0].uid)
    assert idx == 1
    assert second.uid == sample_history_items[0].uid

    # Check: history_pop_item returns the correct item
    removed = await backend.history_pop_item()
    assert removed.uid == sample_history_items[1].uid

    # Check: history_length works as intended, and history_pop_item worked as intended
    current_length = await backend.history_length()
    assert current_length == 1

    await backend.history_insert_item(sample_history_items[1], index=None)

    # Check: history_get, history_insert_item inserted at the correct place with the index parameter
    current = await backend.history_get()
    assert isinstance(current, list)
    assert len(current) == 2
    assert current[0].uid == sample_history_items[0].uid
    assert current[1].uid == sample_history_items[1].uid

    await backend.history_clear()

    # Check: history_length, history_clear worked as intended
    current_length = await backend.history_length()
    assert current_length == 0


async def test_redis_key_with_no_queue_name():
    backend = RedisPersistenceBackend(mock=True, queue_name="test_queue", no_queue_name_in_key=True)

    assert backend.key_separator() == "_"
    assert backend.full_key_prefix == "qs_default"
