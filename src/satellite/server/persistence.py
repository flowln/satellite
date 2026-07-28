from abc import abstractmethod
from collections.abc import Callable
from functools import cached_property, wraps
import json
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from satellite.annotations import DeviceAnnotation, PlanAnnotation
from satellite.models import HistoryItem, QueueItem

from .configuration import ManagerConfiguration


class PersistenceBackend:
    """Base class for implementing cross-session persistence of data."""

    QUEUE_KEY = "item_queue"
    HISTORY_KEY = "item_history"

    def __init__(self, queue_name: str, key_prefix: str = ""):
        self._queue_name = queue_name
        self._key_prefix = key_prefix

    @cached_property
    def full_key_prefix(self) -> str:
        """Prefix of any key used in the backend."""
        return self._key_prefix + (self.key_separator() if len(self._key_prefix) != 0 else "") + self._queue_name

    def key_separator(self) -> str:
        """Separator of hierarchical levels inside a key."""
        return ":"

    @abstractmethod
    async def get_existing_plans(self, sub_key: str | None = None) -> dict[str, PlanAnnotation] | PlanAnnotation | None:
        """
        Retrieve the stored existing plan annotations.

        Parameters
        ----------
        sub_key : str, optional
            Instead of retrieving all the plan annotations available,
            try retrieving a single one with a specific name.

        Returns
        -------
        dict[str, PlanAnnotation]
            A dictionary mapping plan names to their annotations
        PlanAnnotation
            If 'sub_key' was used and a match was found, the annotation
            for that plan is returned directly.
        None
            If no matches were found, None is returned.
        """
        ...

    @abstractmethod
    async def set_existing_plans(self, value: dict[str, PlanAnnotation]) -> bool:
        """Store existing plan annotations for later retrieval."""
        ...

    @abstractmethod
    async def get_existing_devices(
        self, sub_key: str | None = None
    ) -> dict[str, DeviceAnnotation] | DeviceAnnotation | None:
        """
        Retrieve the stored existing device annotations.

        Parameters
        ----------
        sub_key : str, optional
            Instead of retrieving all the device annotations available,
            try retrieving a single one with a specific name.

        Returns
        -------
        dict[str, DeviceAnnotation]
            A dictionary mapping device names to their annotations
        DeviceAnnotation
            If 'sub_key' was used and a match was found, the annotation
            for that device is returned directly.
        None
            If no matches were found, None is returned.
        """
        ...

    @abstractmethod
    async def set_existing_devices(self, value: dict[str, DeviceAnnotation]) -> bool:
        """Store existing device annotations for later retrieval."""
        ...

    @abstractmethod
    async def _list_get(self, key: str, offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def _list_get_item(
        self, key: str, index: int | None = None, uuid: UUID | str | None = None
    ) -> tuple[int, dict[str, Any]]: ...

    @abstractmethod
    async def _list_insert_item(self, key: str, item: Any, index: int | None = 0) -> bool: ...

    @abstractmethod
    async def _list_pop_item(self, key: str, index: int = 0) -> dict[str, Any]: ...

    @abstractmethod
    async def _list_clear(self, key: str) -> bool: ...

    @abstractmethod
    async def _list_length(self, key: str) -> int: ...

    async def queue_get(self, offset: int = 0, limit: int | None = None) -> list[QueueItem]:
        """
        Retrieve a subset of items currently on the queue.

        Parameters
        ----------
        offset : int, optional
            Offset into the entire list of items from which to start retrieving items. Defaults to 0.
        limit : int, optional
            Maximum amount of items to retrieve. Defaults to None (no limit).
        """
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        raw_items = await self._list_get(key, offset, limit)
        return [QueueItem.model_validate(_i, by_alias=True) for _i in raw_items]

    async def queue_get_item(self, index: int | None = None, uuid: UUID | str | None = None) -> tuple[int, QueueItem]:
        """
        Retrieve a single item from the queue.

        Parameters
        ----------
        index : int, optional
            Retrieve the item at the given index.
        uuid : UUID or str, optional
            Retrieve the item with the specified uid.

        Returns
        -------
        tuple[int, QueueItem]
            A pair consisting of the current index of the returned item, and the item itself.
        """
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        idx, raw_item = await self._list_get_item(key, index, uuid)
        return idx, QueueItem.model_validate(raw_item, by_alias=True)

    async def queue_insert_item(self, item: QueueItem, index: int | None = 0) -> bool:
        """
        Insert a new item on the queue.

        Parameters
        ----------
        item : QueueItem
            The item to be added into the queue.
        index : int, optional
            The place in which the item is to be added. The special value 'None' specifies
            that the item should be added at the very end of the queue. Defaults to 0.
        """
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        return await self._list_insert_item(key, item, index)

    async def queue_pop_item(self, index: int = 0) -> QueueItem:
        """
        Remove an item from the queue.

        Parameters
        ----------
        index : int, optional
            The place from which to remove the item. Defaults to 0.
        """
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        raw_item = await self._list_pop_item(key, index)
        return QueueItem.model_validate(raw_item, by_alias=True)

    async def queue_clear(self) -> bool:
        """Remove all items currently in the queue."""
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        return await self._list_clear(key)

    async def queue_length(self) -> int:
        """Return the current amount of items in the queue."""
        key = self.full_key_prefix + self.key_separator() + self.QUEUE_KEY
        return await self._list_length(key)

    async def history_get(self, offset: int = 0, limit: int | None = None) -> list[HistoryItem]:
        """
        Retrieve a subset of items currently on the history.

        Parameters
        ----------
        offset : int, optional
            Offset into the entire list of items from which to start retrieving items. Defaults to 0.
        limit : int, optional
            Maximum amount of items to retrieve. Defaults to None (no limit).
        """
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        raw_items = await self._list_get(key, offset, limit)
        return [HistoryItem.model_validate(_i, by_alias=True) for _i in raw_items]

    async def history_get_item(
        self, index: int | None = None, uuid: UUID | str | None = None
    ) -> tuple[int, HistoryItem]:
        """
        Retrieve a single item from the history.

        Parameters
        ----------
        index : int, optional
            Retrieve the item at the given index.
        uuid : UUID or str, optional
            Retrieve the item with the specified uid.

        Returns
        -------
        tuple[int, HistoryItem]
            A pair consisting of the current index of the returned item, and the item itself.
        """
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        idx, raw_item = await self._list_get_item(key, index, uuid)
        return idx, HistoryItem.model_validate(raw_item, by_alias=True)

    async def history_insert_item(self, item: HistoryItem, index: int | None = 0) -> bool:
        """
        Insert a new item on the history.

        Parameters
        ----------
        item : HistoryItem
            The item to be added into the history.
        index : int, optional
            The place in which the item is to be added. The special value 'None' specifies
            that the item should be added at the very end of the history. Defaults to 0.
        """
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        return await self._list_insert_item(key, item, index)

    async def history_pop_item(self, index: int = 0) -> HistoryItem:
        """
        Remove an item from the history.

        Parameters
        ----------
        index : int, optional
            The place from which to remove the item. Defaults to 0.
        """
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        raw_item = await self._list_pop_item(key, index)
        return HistoryItem.model_validate(raw_item, by_alias=True)

    async def history_clear(self) -> bool:
        """Remove all items currently in the history."""
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        return await self._list_clear(key)

    async def history_length(self) -> int:
        """Return the current amount of items in the history."""
        key = self.full_key_prefix + self.key_separator() + self.HISTORY_KEY
        return await self._list_length(key)


class RedisPersistenceBackend(PersistenceBackend):
    """Redis-backed persistence storage."""

    EXISTING_PLANS_KEY = "existing_plans"
    EXISTING_DEVICES_KEY = "existing_devices"

    def __init__(
        self,
        address: str | None = None,
        mock: bool = False,
        *args,
        mock_fake_server: object | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if address is None:
            address = "redis://localhost"

        if not mock:
            from redis.asyncio import from_url

            self._client = from_url(address)
        else:
            from fakeredis import FakeAsyncRedis, FakeServer

            assert isinstance(mock_fake_server, (type(None), FakeServer))  # noqa
            self._client = FakeAsyncRedis(server=mock_fake_server)

        from redis.exceptions import ResponseError

        self.response_error = ResponseError

    async def _get_with_sub_key(self, item_type: type[BaseModel], key: str, sub_key: str | None = None):
        if sub_key is None:
            try:
                value = await self._client.json().get(key)
            except self.response_error:
                return None

            if not isinstance(value, dict):
                return None

            return_value = {}
            for _k, _v in value.items():
                return_value[_k] = item_type.model_validate(_v, by_alias=True)
        else:
            try:
                value = await self._client.json().get(key, f'$.["{sub_key}"]')
            except self.response_error:
                return None

            if not isinstance(value, list):
                return None
            if len(value) == 0:
                return None

            return_value = item_type.model_validate(value[0], by_alias=True)

        return return_value

    async def _ensure_initialized(self, key: str, *, default_factory: Callable = list):
        await self._client.json().set(key, "$", default_factory(), nx=True)

    @wraps(PersistenceBackend.get_existing_plans)
    async def get_existing_plans(self, sub_key: str | None = None) -> dict[str, PlanAnnotation] | PlanAnnotation | None:  # noqa
        key = self.full_key_prefix + ":" + self.EXISTING_PLANS_KEY
        await self._ensure_initialized(key, default_factory=dict)

        return await self._get_with_sub_key(PlanAnnotation, key, sub_key)

    @wraps(PersistenceBackend.set_existing_plans)
    async def set_existing_plans(self, value: dict[str, PlanAnnotation]) -> bool:  # noqa
        key = self.full_key_prefix + ":" + self.EXISTING_PLANS_KEY
        await self._ensure_initialized(key, default_factory=dict)

        value_serialized = {_k: _v.model_dump(mode="json", by_alias=True) for _k, _v in value.items()}
        return bool(await self._client.json().set(key, "$", value_serialized))

    @wraps(PersistenceBackend.get_existing_devices)
    async def get_existing_devices(  # noqa
        self, sub_key: str | None = None
    ) -> dict[str, DeviceAnnotation] | DeviceAnnotation | None:
        key = self.full_key_prefix + ":" + self.EXISTING_DEVICES_KEY
        await self._ensure_initialized(key, default_factory=dict)

        return await self._get_with_sub_key(DeviceAnnotation, key, sub_key)

    @wraps(PersistenceBackend.set_existing_devices)
    async def set_existing_devices(self, value: dict[str, DeviceAnnotation]) -> bool:  # noqa
        key = self.full_key_prefix + ":" + self.EXISTING_DEVICES_KEY
        await self._ensure_initialized(key, default_factory=dict)

        value_serialized = {_k: _v.model_dump(mode="json", by_alias=True) for _k, _v in value.items()}
        return bool(await self._client.json().set(key, "$", value_serialized))

    async def _list_get(self, key: str, offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        await self._ensure_initialized(key)

        if limit is not None:
            length = await self._list_length(key)
            limit = min(limit, length - offset - 1)  # Do not go further than the last element.

            raw_queue = cast(
                list,
                await self._client.json().get(key, f"$.[{offset}:{offset + limit}]"),
            )
        else:
            raw_queue = cast(list, await self._client.json().get(key, f"$.[{offset}:]"))

        if len(raw_queue) != 0 and isinstance(raw_queue[0], str):
            raw_queue = [json.loads(_i) for _i in raw_queue]
        return cast(list[dict], raw_queue)

    async def _list_get_item(
        self, key: str, index: int | None = None, uuid: UUID | str | None = None
    ) -> tuple[int, dict[str, Any]]:
        await self._ensure_initialized(key)

        if uuid is not None:
            # FIXME: Find a more optimized way of doing this.
            _entire_list = await self._list_get(key)
            for _idx, _item in enumerate(_entire_list):
                if _item["item_uid"] == str(uuid):
                    index = _idx

                    break

        if index is None or (isinstance(index, list) and len(index) == 0):
            raise RuntimeError(f"Failed to get item from queue with {index=} and {uuid=}")

        raw_items = await self._client.json().get(key, f"$.[{index}]")
        if isinstance(raw_items, list) and len(raw_items) == 1:
            if isinstance(raw_items[0], str):
                return index, json.loads(raw_items[0])
            return index, cast(dict, raw_items[0])

        raise RuntimeError(f"Failed to properly retrieve item ({index=} {uuid=}) from Redis: {raw_items}")

    async def _list_insert_item(self, key: str, item: QueueItem, index: int | None = 0) -> bool:
        await self._ensure_initialized(key)

        item_serialized = item.model_dump(mode="json", by_alias=True)

        if index is None:
            return (
                cast(
                    list[int],
                    await self._client.json().arrappend(key, "$", item_serialized),
                )[0]
                >= 1
            )

        return (await self._client.json().arrinsert(key, "$", index, item_serialized)) == 1

    async def _list_pop_item(self, key: str, index: int = 0) -> dict[str, Any]:
        await self._ensure_initialized(key)

        raw_items = await self._client.json().arrpop(key, "$", index=index)
        if isinstance(raw_items, list) and len(raw_items) == 1:
            if isinstance(raw_items[0], str):
                return json.loads(raw_items[0])
            return cast(dict, raw_items[0])

        raise RuntimeError(f"Failed to properly pop item ({index=}) from Redis: {raw_items}")

    async def _list_clear(self, key: str) -> bool:
        await self._ensure_initialized(key)

        return (await self._client.json().clear(key, "$")) != 0

    async def _list_length(self, key: str) -> int:
        await self._ensure_initialized(key)

        return cast(int, await self._client.json().arrlen(key))


def create_backend_for_configuration(queue_name: str, config: ManagerConfiguration) -> PersistenceBackend:
    """Create a PersistenceBackend object as specified by 'config' for the queue named 'queue_name'."""
    match config.network.persistence_backend:
        case "none":
            raise NotImplementedError
        case "redis":
            return RedisPersistenceBackend(
                address=config.network.redis_address,
                queue_name=queue_name,
                key_prefix=config.network.redis_name_prefix,
                mock=config.network.use_mocked_backend,
                **config.network.mock_arguments,
            )
