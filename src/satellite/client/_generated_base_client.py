"""
This file is auto-generated! Do NOT manually edit it.

Instead, check 'main.py' for the logic that generates it or,
if applicable, change the final client code in 'client.py' instead.

This file was generated at:
Date: 2026-08-13T21:04+00:00
Git revision: dc1e1e6ace8e579b7266769d9535dd2334aed6d4
"""

from abc import abstractmethod
import asyncio
from collections.abc import Coroutine
import logging
from typing import Any, Literal
from uuid import UUID

import httpx

from satellite.models import (
    AllowedDevicesResponse,
    AllowedPlansResponse,
    ConsoleUidResponse,
    ExecutionConfiguration,
    GenericResponse,
    HistoryResponse,
    LatestConsoleResponse,
    LockResponse,
    ManagerStatus,
    QueueAddRemoveBatchResponse,
    QueueAddRemoveResponse,
    QueueItem,
    QueueResponse,
    RunEngineRunsResponse,
    SuccessfulLoginResponse,
    UserInformation,
)

logger = logging.getLogger("satellite.client")


class BaseAsyncClient(httpx.AsyncClient):
    def __init__(self, server_address: httpx.URL | str, queue_name: str | None = None, **kwargs):
        self._base_url = server_address
        self._queue_name = queue_name
        super().__init__(base_url=self.base_address, **kwargs)

    @property
    def queue_name(self) -> str | None:
        return self._queue_name

    @property
    def base_address(self) -> httpx.URL:
        if self.queue_name is None:
            return httpx.URL(self._base_url)
        return httpx.URL(self._base_url.join("/" + self.queue_name))

    async def get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    @abstractmethod
    async def login(
        self, user_name: str, password: str, *, expiration_time: int | float | None = None
    ) -> SuccessfulLoginResponse: ...

    @abstractmethod
    async def logout(self): ...

    @abstractmethod
    async def refresh_session(self, *, expiration_time: int | float | None = None) -> SuccessfulLoginResponse: ...

    @abstractmethod
    async def whoami(self) -> UserInformation: ...

    async def ping(self) -> dict[Literal["message"], Literal["pong"]]:
        """Test connectivity with the queue manager. Always responds 'pong'."""
        parameters = {}
        response = await self.get_implementation("/ping", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = response.json()
        return ret

    async def status(self) -> ManagerStatus:
        """Retrieve the current state of the manager."""
        parameters = {}
        response = await self.get_implementation("/status", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = ManagerStatus.model_validate(response.json())
        return ret

    async def lock(
        self, lock_key: str, environment: bool = False, queue: bool = False, note: str | None = None
    ) -> LockResponse:
        """
        Lock the manager, preventing other users from accessing some write endpoints.

        Parameters
        ----------
        lock_key : str
            The lock key currently being used.
        environment : bool, optional
            Lock environment operations (open, close, RunEngine operations).
        queue : bool, optional
            Lock queue operations (add, remove, move items on the queue).
        note : str, optional
            Optional message to leave to other users to clarify why the manager is currently locked.
        user : str, optional
            The user who is locking the manager.
        """
        default_values = {"environment": False, "queue": False, "note": None}
        original_parameters = {"lock_key": lock_key, "environment": environment, "queue": queue, "note": note}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/lock", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LockResponse.model_validate(response.json())
        return ret

    async def unlock(self, lock_key: str) -> LockResponse:
        """
        Unlock the manager, allowing other users to access write endpoints.

        Parameters
        ----------
        lock_key : str
            The lock key currently being used.
        """
        default_values = {}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/unlock", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LockResponse.model_validate(response.json())
        return ret

    async def queue_mode_set(
        self,
        mode: ExecutionConfiguration | Literal["default"] | None = None,
        loop: bool | None = None,
        ignore_failures: bool | None = None,
        autostart: bool | None = None,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Configure item execution behavior for queue items.

        Parameters
        ----------
        mode : dict or "default", optional
            The new item execution mode to configure. Either a dictionary with the individual configurations to set,
            or a string "default" to return all configurations to their default values.
        loop : bool, optional
            The loop mode to configure. Activating this mode means that recently executed items get added back to the
            end of the queue, and execution continues from there according to the auto-start mode.
        ignore_failures : bool, optional
            Whether to continue queue execution after an item has failed (or aborted or halted) its execution.
        autostart : bool, optional
            If enabled, start item execution whenever possible (i.e. there's at least one item on the queue, the
            environment is ready for execution, and the previous item didn't stop the queue execution somehow).
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"mode": None, "loop": None, "ignore_failures": None, "autostart": None, "lock_key": None}
        original_parameters = {
            "mode": mode,
            "loop": loop,
            "ignore_failures": ignore_failures,
            "autostart": autostart,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/mode/set", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def queue_autostart(self, enable: bool, lock_key: str | None = None) -> GenericResponse:
        """
        Configure auto-start item execution behavior for queue items.

        Parameters
        ----------
        enable : bool
            If enabled, start item execution whenever possible (i.e. there's at least one item on the queue, the
            environment is ready for execution, and the previous item didn't stop the queue execution somehow).
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"enable": enable, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/autostart", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def environment_open(self, lock_key: str | None = None) -> GenericResponse:
        """
        Open a new environment for plan execution.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/environment/open", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def environment_close(self, lock_key: str | None = None) -> GenericResponse:
        """
        Close the currently active environment.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/environment/close", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def environment_destroy(self, lock_key: str | None = None) -> GenericResponse:
        """
        Destroy the currently active environment, without cleaning up anything.

        This should only be used when the environment is stuck, since it can cause
        unexpected behavior and corrupted states.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/environment/destroy", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def history_get(self, limit: int | None = None, offset: int = 0) -> HistoryResponse:
        """
        Retrieve information about previously ran plans.

        Parameters
        ----------
        limit : int, optional
            Retrieve up to 'limit' entries. A value of 'None' (default)
            fetches all history entries until the oldest one available.
        offset : int, optional
            Offset from which to start retrieving items. The offset
            starts at the newest entry, and goes up from there.

        """
        default_values = {"limit": None, "offset": 0}
        original_parameters = {"limit": limit, "offset": offset}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.get_implementation("/history/get", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = HistoryResponse.model_validate(response.json())
        return ret

    async def history_clear(self) -> GenericResponse:
        """Clear the history of previously ran plans."""
        parameters = {}
        response = await self.post_implementation("/history/clear", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def queue_get(self) -> QueueResponse:
        """Retrieve a list of all items currently in the queue."""
        parameters = {}
        response = await self.get_implementation("/queue/get", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueResponse.model_validate(response.json())
        return ret

    async def queue_clear(self, lock_key: str | None = None) -> GenericResponse:
        """
        Remove all items currently in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/clear", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def queue_item_add(
        self,
        item: QueueItem,
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveResponse:
        """
        Add a new item to the queue.

        Parameters
        ----------
        item : QueueItem
            The item to add to the queue.

            The 'item_uid' field is expected to be null, as it will be filled up after this call.
        pos : int, "back" or "front", optional
            The position in which to add this item in the queue.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": "back", "before_uid": None, "after_uid": None, "lock_key": None}
        original_parameters = {
            "item": item,
            "pos": pos,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/add", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    async def queue_item_add_batch(
        self,
        items: list[QueueItem],
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveBatchResponse:
        """
        Add new items to the queue.

        Parameters
        ----------
        items : sequence of QueueItem
            The items to add to the queue.

            The 'item_uid' field is expected to be null, as it will be filled up after this call.
        pos : int, "back" or "front", optional
            The position in which to add this item in the queue.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": "back", "before_uid": None, "after_uid": None, "lock_key": None}
        original_parameters = {
            "items": items,
            "pos": pos,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/add/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    async def queue_item_remove(
        self, pos: int | Literal["front", "back"] | None = None, uid: str | None = None, lock_key: str | None = None
    ) -> QueueAddRemoveResponse:
        """
        Remove a single item from the queue.

        Parameters
        ----------
        pos : int, "back" or "front", optional
            Remove the item at the specified position.

            "back" means removing the last item in the current queue.

            "front" means removing the first item in the current queue.

            An integer specifies the index of the item to remove.

            This option cannot be specified at the same time as 'uid'.
        uid : str, optional
            Remove the item with the specified uid.

            This option cannot be specified at the same time as 'pos'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": None, "uid": None, "lock_key": None}
        original_parameters = {"pos": pos, "uid": uid, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/remove", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    async def queue_item_remove_batch(
        self, uids: list[UUID], ignore_missing: bool = True, lock_key: str | None = None
    ) -> QueueAddRemoveBatchResponse:
        """
        Remove multiple items from the queue.

        Parameters
        ----------
        uids : sequence of UUIDs
            Remove all items with the given UUIDs.
        ignore_missing : bool, optional
            If True (default), remove all items matching the given UUIDs, and ignore
            any missing items. Otherwise, any missing items will fail the operation,
            and the method will return the items that have been removed.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"ignore_missing": True, "lock_key": None}
        original_parameters = {"uids": uids, "ignore_missing": ignore_missing, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/remove/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    async def queue_item_update(
        self, item: QueueItem, replace: bool = False, lock_key: str | None = None
    ) -> QueueAddRemoveResponse:
        """
        Add new items to the queue.

        Parameters
        ----------
        item : QueueItem
            A queue item with updated information to commit to the queue.

            The 'item_uid' field is expected to be filled, as the item to be updated is determined from it.
        replace : bool, optional
            Whether to replace the existing item. The practial consequence of using this option is that a new uid
            is generated for the item, instead of keeping the uid of the replaced item. False by default.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"replace": False, "lock_key": None}
        original_parameters = {"item": item, "replace": replace, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/update", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    async def queue_item_move(
        self,
        pos: int | Literal["front", "back"] | None = None,
        uid: UUID | None = None,
        pos_dest: int | Literal["front", "back"] | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveResponse:
        """
        Move an item to another position on the queue.

        Parameters
        ----------
        pos : int, "back" or "front", optional
            The original position of the item to move.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'uid'.
        uid : UUID, optional
            The unique identifier of the item to move.

            This option cannot be specified at the same time as 'pos'.
        pos_dest : int, "back" or "front", optional
            The position to move the item to.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {
            "pos": None,
            "uid": None,
            "pos_dest": None,
            "before_uid": None,
            "after_uid": None,
            "lock_key": None,
        }
        original_parameters = {
            "pos": pos,
            "uid": uid,
            "pos_dest": pos_dest,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/move", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    async def queue_item_move_batch(
        self,
        uids: list[UUID],
        pos_dest: int | Literal["front", "back"] | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
        reorder: bool = False,
        lock_key: str | None = None,
    ) -> QueueAddRemoveBatchResponse:
        """
        Move some items to another position on the queue.

        Parameters
        uids : sequence of UUIDs
            The unique identifiers of the items to move.
        pos_dest : int, "back" or "front", optional
            The position to move the item to.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        reorder : bool, optional
            If True, reorder the moved items to be in the same order as the 'uids' sequence.
            Otherwise (default), keep the original ordering of items.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos_dest": None, "before_uid": None, "after_uid": None, "reorder": False, "lock_key": None}
        original_parameters = {
            "uids": uids,
            "pos_dest": pos_dest,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "reorder": reorder,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/move/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    async def queue_item_execute(self, item: QueueItem, lock_key: str | None = None) -> QueueAddRemoveResponse:
        """
        Immediately execute an item, bypassing the queue current state and options.

        Parameters
        ----------
        item : QueueItem
            The item to execute immediately.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"item": item, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/item/execute", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    async def queue_start(self, lock_key: str | None = None) -> GenericResponse:
        """
        Start execution of the items in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/start", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def queue_stop(self, lock_key: str | None = None) -> GenericResponse:
        """
        Stop execution of the items in the queue.

        This will mark the manager for stopping, which will be applied when the currently
        running item finishes executing (i.e. stop after the current item finishes).

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/stop", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def queue_stop_cancel(self, lock_key: str | None = None) -> GenericResponse:
        """
        Ensure the queue continues executing after the current item.

        This will clear the mark left by a previous call to `queue_stop`, so that
        it now continues execution after the current item finishes executing.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/queue/stop/cancel", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_pause(
        self, option: Literal["immediate", "deferred"] = "deferred", lock_key: str | None = None
    ) -> GenericResponse:
        """
        Send a request to the RunEngine for pausing plan execution.

        Parameters
        ----------
        option : "immediate" or "deferred", optional
            How the pause should happen:

                Immediate means pausing right now and going back to the previous checkpoint.

                Deferred means waiting for the next checkpoint to be reached before pausing.

            Defaults to 'deferred'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"option": "deferred", "lock_key": None}
        original_parameters = {"option": option, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/re/pause", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_resume(self, lock_key: str | None = None) -> GenericResponse:
        """
        Resume execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/re/resume", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_stop(self, lock_key: str | None = None) -> GenericResponse:
        """
        Stop execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/re/stop", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_abort(self, lock_key: str | None = None) -> GenericResponse:
        """
        Abort execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/re/abort", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_halt(self, lock_key: str | None = None) -> GenericResponse:
        """
        Halt execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.post_implementation("/re/halt", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    async def re_runs(self, option: Literal["active", "open", "closed"] = "active") -> RunEngineRunsResponse:
        """
        Retrieve the list of runs in the current plan execution.

        Parameters
        ----------
        option : active, open or closed, optional
            Which set of runs to return:

            `active`: Return all runs from the current execution. (default)

            `open`: Return only the runs that have yet to emit a 'stop' document.

            `closed`: Return only the runs that have already emitted a 'stop' document.
        """
        default_values = {"option": "active"}
        original_parameters = {"option": option}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.get_implementation("/re/runs", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = RunEngineRunsResponse.model_validate(response.json())
        return ret

    async def console_output(self, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output.

        Parameters
        ----------
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        default_values = {"lines": 200}
        original_parameters = {"lines": lines}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.get_implementation("/console_output", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    async def console_output_update(self, last_msg_uid: UUID | None = None, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output, generated after some point.

        Parameters
        ----------
        last_msg_uid : UUID or str
            The uid (as returned by `/console_output/uid`) from which to start collecting lines.
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        default_values = {"last_msg_uid": None, "lines": 200}
        original_parameters = {"last_msg_uid": last_msg_uid, "lines": lines}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = await self.get_implementation("/console_output_update", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    async def console_output_uid(self) -> ConsoleUidResponse:
        """
        Get a unique identifier for the current state of the console output.

        This identifier has the property that anytime a new line is appended to
        the console, a new uid is generated.
        """
        parameters = {}
        response = await self.get_implementation("/console_output/uid", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = ConsoleUidResponse.model_validate(response.json())
        return ret

    async def plans_allowed(self) -> AllowedPlansResponse:
        """Retrieve a list of allowed plans for the current user."""
        parameters = {}
        response = await self.get_implementation("/plans/allowed", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = AllowedPlansResponse.model_validate(response.json())
        return ret

    async def devices_allowed(self) -> AllowedDevicesResponse:
        """Retrieve a list of allowed devices for the current user."""
        parameters = {}
        response = await self.get_implementation("/devices/allowed", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = AllowedDevicesResponse.model_validate(response.json())
        return ret


class BaseSyncClient:
    def __init__(self, server_address: httpx.URL | str, queue_name: str | None = None, **kwargs):
        self._loop = asyncio.new_event_loop()
        self._client = BaseAsyncClient(server_address, queue_name, **kwargs)
        raise NotImplementedError

    @property
    def queue_name(self) -> str | None:
        return self._client.queue_name

    @property
    def base_address(self) -> httpx.URL:
        return self._client.base_address

    def _run_coroutine(self, coro: Coroutine) -> Any:
        result = None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            task = self._loop.create_task(coro)
            result = self._loop.run_until_complete(task)
        else:

            def _execute_coro():
                nonlocal result
                result = self._loop.run_until_complete(coro)

            import threading

            worker = threading.Thread(target=_execute_coro, daemon=True)
            worker.start()
            worker.join(timeout=10.0)
        return result

    def get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        return self._run_coroutine(self._client.get_implementation(endpoint, **kwargs))

    def post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        return self._run_coroutine(self._client.post_implementation(endpoint, **kwargs))

    def ping(self) -> dict[Literal["message"], Literal["pong"]]:
        """Test connectivity with the queue manager. Always responds 'pong'."""
        parameters = {}
        response = self.get_implementation("/ping", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = response.json()
        return ret

    def status(self) -> ManagerStatus:
        """Retrieve the current state of the manager."""
        parameters = {}
        response = self.get_implementation("/status", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = ManagerStatus.model_validate(response.json())
        return ret

    def lock(
        self, lock_key: str, environment: bool = False, queue: bool = False, note: str | None = None
    ) -> LockResponse:
        """
        Lock the manager, preventing other users from accessing some write endpoints.

        Parameters
        ----------
        lock_key : str
            The lock key currently being used.
        environment : bool, optional
            Lock environment operations (open, close, RunEngine operations).
        queue : bool, optional
            Lock queue operations (add, remove, move items on the queue).
        note : str, optional
            Optional message to leave to other users to clarify why the manager is currently locked.
        user : str, optional
            The user who is locking the manager.
        """
        default_values = {"environment": False, "queue": False, "note": None}
        original_parameters = {"lock_key": lock_key, "environment": environment, "queue": queue, "note": note}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/lock", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LockResponse.model_validate(response.json())
        return ret

    def unlock(self, lock_key: str) -> LockResponse:
        """
        Unlock the manager, allowing other users to access write endpoints.

        Parameters
        ----------
        lock_key : str
            The lock key currently being used.
        """
        default_values = {}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/unlock", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LockResponse.model_validate(response.json())
        return ret

    def queue_mode_set(
        self,
        mode: ExecutionConfiguration | Literal["default"] | None = None,
        loop: bool | None = None,
        ignore_failures: bool | None = None,
        autostart: bool | None = None,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Configure item execution behavior for queue items.

        Parameters
        ----------
        mode : dict or "default", optional
            The new item execution mode to configure. Either a dictionary with the individual configurations to set,
            or a string "default" to return all configurations to their default values.
        loop : bool, optional
            The loop mode to configure. Activating this mode means that recently executed items get added back to the
            end of the queue, and execution continues from there according to the auto-start mode.
        ignore_failures : bool, optional
            Whether to continue queue execution after an item has failed (or aborted or halted) its execution.
        autostart : bool, optional
            If enabled, start item execution whenever possible (i.e. there's at least one item on the queue, the
            environment is ready for execution, and the previous item didn't stop the queue execution somehow).
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"mode": None, "loop": None, "ignore_failures": None, "autostart": None, "lock_key": None}
        original_parameters = {
            "mode": mode,
            "loop": loop,
            "ignore_failures": ignore_failures,
            "autostart": autostart,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/mode/set", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def queue_autostart(self, enable: bool, lock_key: str | None = None) -> GenericResponse:
        """
        Configure auto-start item execution behavior for queue items.

        Parameters
        ----------
        enable : bool
            If enabled, start item execution whenever possible (i.e. there's at least one item on the queue, the
            environment is ready for execution, and the previous item didn't stop the queue execution somehow).
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"enable": enable, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/autostart", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def environment_open(self, lock_key: str | None = None) -> GenericResponse:
        """
        Open a new environment for plan execution.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/environment/open", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def environment_close(self, lock_key: str | None = None) -> GenericResponse:
        """
        Close the currently active environment.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/environment/close", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def environment_destroy(self, lock_key: str | None = None) -> GenericResponse:
        """
        Destroy the currently active environment, without cleaning up anything.

        This should only be used when the environment is stuck, since it can cause
        unexpected behavior and corrupted states.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/environment/destroy", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def history_get(self, limit: int | None = None, offset: int = 0) -> HistoryResponse:
        """
        Retrieve information about previously ran plans.

        Parameters
        ----------
        limit : int, optional
            Retrieve up to 'limit' entries. A value of 'None' (default)
            fetches all history entries until the oldest one available.
        offset : int, optional
            Offset from which to start retrieving items. The offset
            starts at the newest entry, and goes up from there.

        """
        default_values = {"limit": None, "offset": 0}
        original_parameters = {"limit": limit, "offset": offset}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.get_implementation("/history/get", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = HistoryResponse.model_validate(response.json())
        return ret

    def history_clear(self) -> GenericResponse:
        """Clear the history of previously ran plans."""
        parameters = {}
        response = self.post_implementation("/history/clear", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def queue_get(self) -> QueueResponse:
        """Retrieve a list of all items currently in the queue."""
        parameters = {}
        response = self.get_implementation("/queue/get", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueResponse.model_validate(response.json())
        return ret

    def queue_clear(self, lock_key: str | None = None) -> GenericResponse:
        """
        Remove all items currently in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/clear", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def queue_item_add(
        self,
        item: QueueItem,
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveResponse:
        """
        Add a new item to the queue.

        Parameters
        ----------
        item : QueueItem
            The item to add to the queue.

            The 'item_uid' field is expected to be null, as it will be filled up after this call.
        pos : int, "back" or "front", optional
            The position in which to add this item in the queue.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": "back", "before_uid": None, "after_uid": None, "lock_key": None}
        original_parameters = {
            "item": item,
            "pos": pos,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/add", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    def queue_item_add_batch(
        self,
        items: list[QueueItem],
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveBatchResponse:
        """
        Add new items to the queue.

        Parameters
        ----------
        items : sequence of QueueItem
            The items to add to the queue.

            The 'item_uid' field is expected to be null, as it will be filled up after this call.
        pos : int, "back" or "front", optional
            The position in which to add this item in the queue.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": "back", "before_uid": None, "after_uid": None, "lock_key": None}
        original_parameters = {
            "items": items,
            "pos": pos,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/add/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    def queue_item_remove(
        self, pos: int | Literal["front", "back"] | None = None, uid: str | None = None, lock_key: str | None = None
    ) -> QueueAddRemoveResponse:
        """
        Remove a single item from the queue.

        Parameters
        ----------
        pos : int, "back" or "front", optional
            Remove the item at the specified position.

            "back" means removing the last item in the current queue.

            "front" means removing the first item in the current queue.

            An integer specifies the index of the item to remove.

            This option cannot be specified at the same time as 'uid'.
        uid : str, optional
            Remove the item with the specified uid.

            This option cannot be specified at the same time as 'pos'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos": None, "uid": None, "lock_key": None}
        original_parameters = {"pos": pos, "uid": uid, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/remove", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    def queue_item_remove_batch(
        self, uids: list[UUID], ignore_missing: bool = True, lock_key: str | None = None
    ) -> QueueAddRemoveBatchResponse:
        """
        Remove multiple items from the queue.

        Parameters
        ----------
        uids : sequence of UUIDs
            Remove all items with the given UUIDs.
        ignore_missing : bool, optional
            If True (default), remove all items matching the given UUIDs, and ignore
            any missing items. Otherwise, any missing items will fail the operation,
            and the method will return the items that have been removed.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"ignore_missing": True, "lock_key": None}
        original_parameters = {"uids": uids, "ignore_missing": ignore_missing, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/remove/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    def queue_item_update(
        self, item: QueueItem, replace: bool = False, lock_key: str | None = None
    ) -> QueueAddRemoveResponse:
        """
        Add new items to the queue.

        Parameters
        ----------
        item : QueueItem
            A queue item with updated information to commit to the queue.

            The 'item_uid' field is expected to be filled, as the item to be updated is determined from it.
        replace : bool, optional
            Whether to replace the existing item. The practial consequence of using this option is that a new uid
            is generated for the item, instead of keeping the uid of the replaced item. False by default.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"replace": False, "lock_key": None}
        original_parameters = {"item": item, "replace": replace, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/update", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    def queue_item_move(
        self,
        pos: int | Literal["front", "back"] | None = None,
        uid: UUID | None = None,
        pos_dest: int | Literal["front", "back"] | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
        lock_key: str | None = None,
    ) -> QueueAddRemoveResponse:
        """
        Move an item to another position on the queue.

        Parameters
        ----------
        pos : int, "back" or "front", optional
            The original position of the item to move.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'uid'.
        uid : UUID, optional
            The unique identifier of the item to move.

            This option cannot be specified at the same time as 'pos'.
        pos_dest : int, "back" or "front", optional
            The position to move the item to.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {
            "pos": None,
            "uid": None,
            "pos_dest": None,
            "before_uid": None,
            "after_uid": None,
            "lock_key": None,
        }
        original_parameters = {
            "pos": pos,
            "uid": uid,
            "pos_dest": pos_dest,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/move", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    def queue_item_move_batch(
        self,
        uids: list[UUID],
        pos_dest: int | Literal["front", "back"] | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
        reorder: bool = False,
        lock_key: str | None = None,
    ) -> QueueAddRemoveBatchResponse:
        """
        Move some items to another position on the queue.

        Parameters
        uids : sequence of UUIDs
            The unique identifiers of the items to move.
        pos_dest : int, "back" or "front", optional
            The position to move the item to.

            "back" (default) means adding it as the last item in the current queue.

            "front" means adding it as the first item in the current queue.

            An integer specifies an index in which to insert the item into.

            This option cannot be specified at the same time as 'before_uid' or 'after_uid'.
        before_uid : str, optional
            Insert the item before (i.e. executes first) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'after_uid'.
        after_uid : str, optional
            Insert the item after (i.e. executes afterwards) the item with the specified uid.

            This option cannot be specified at the same time as 'pos' or 'before_uid'.
        reorder : bool, optional
            If True, reorder the moved items to be in the same order as the 'uids' sequence.
            Otherwise (default), keep the original ordering of items.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"pos_dest": None, "before_uid": None, "after_uid": None, "reorder": False, "lock_key": None}
        original_parameters = {
            "uids": uids,
            "pos_dest": pos_dest,
            "before_uid": before_uid,
            "after_uid": after_uid,
            "reorder": reorder,
            "lock_key": lock_key,
        }
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/move/batch", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveBatchResponse.model_validate(response.json())
        return ret

    def queue_item_execute(self, item: QueueItem, lock_key: str | None = None) -> QueueAddRemoveResponse:
        """
        Immediately execute an item, bypassing the queue current state and options.

        Parameters
        ----------
        item : QueueItem
            The item to execute immediately.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"item": item, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/item/execute", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = QueueAddRemoveResponse.model_validate(response.json())
        return ret

    def queue_start(self, lock_key: str | None = None) -> GenericResponse:
        """
        Start execution of the items in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/start", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def queue_stop(self, lock_key: str | None = None) -> GenericResponse:
        """
        Stop execution of the items in the queue.

        This will mark the manager for stopping, which will be applied when the currently
        running item finishes executing (i.e. stop after the current item finishes).

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/stop", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def queue_stop_cancel(self, lock_key: str | None = None) -> GenericResponse:
        """
        Ensure the queue continues executing after the current item.

        This will clear the mark left by a previous call to `queue_stop`, so that
        it now continues execution after the current item finishes executing.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/queue/stop/cancel", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_pause(
        self, option: Literal["immediate", "deferred"] = "deferred", lock_key: str | None = None
    ) -> GenericResponse:
        """
        Send a request to the RunEngine for pausing plan execution.

        Parameters
        ----------
        option : "immediate" or "deferred", optional
            How the pause should happen:

                Immediate means pausing right now and going back to the previous checkpoint.

                Deferred means waiting for the next checkpoint to be reached before pausing.

            Defaults to 'deferred'.
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"option": "deferred", "lock_key": None}
        original_parameters = {"option": option, "lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/re/pause", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_resume(self, lock_key: str | None = None) -> GenericResponse:
        """
        Resume execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/re/resume", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_stop(self, lock_key: str | None = None) -> GenericResponse:
        """
        Stop execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/re/stop", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_abort(self, lock_key: str | None = None) -> GenericResponse:
        """
        Abort execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/re/abort", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_halt(self, lock_key: str | None = None) -> GenericResponse:
        """
        Halt execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        default_values = {"lock_key": None}
        original_parameters = {"lock_key": lock_key}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.post_implementation("/re/halt", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = GenericResponse.model_validate(response.json())
        return ret

    def re_runs(self, option: Literal["active", "open", "closed"] = "active") -> RunEngineRunsResponse:
        """
        Retrieve the list of runs in the current plan execution.

        Parameters
        ----------
        option : active, open or closed, optional
            Which set of runs to return:

            `active`: Return all runs from the current execution. (default)

            `open`: Return only the runs that have yet to emit a 'stop' document.

            `closed`: Return only the runs that have already emitted a 'stop' document.
        """
        default_values = {"option": "active"}
        original_parameters = {"option": option}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.get_implementation("/re/runs", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = RunEngineRunsResponse.model_validate(response.json())
        return ret

    def console_output(self, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output.

        Parameters
        ----------
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        default_values = {"lines": 200}
        original_parameters = {"lines": lines}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.get_implementation("/console_output", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    def console_output_update(self, last_msg_uid: UUID | None = None, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output, generated after some point.

        Parameters
        ----------
        last_msg_uid : UUID or str
            The uid (as returned by `/console_output/uid`) from which to start collecting lines.
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        default_values = {"last_msg_uid": None, "lines": 200}
        original_parameters = {"last_msg_uid": last_msg_uid, "lines": lines}
        parameters = original_parameters.copy()
        for arg_name in original_parameters.keys():
            if arg_name not in default_values:
                continue
            if original_parameters[arg_name] == default_values[arg_name]:
                del parameters[arg_name]
        response = self.get_implementation("/console_output_update", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    def console_output_uid(self) -> ConsoleUidResponse:
        """
        Get a unique identifier for the current state of the console output.

        This identifier has the property that anytime a new line is appended to
        the console, a new uid is generated.
        """
        parameters = {}
        response = self.get_implementation("/console_output/uid", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = ConsoleUidResponse.model_validate(response.json())
        return ret

    def plans_allowed(self) -> AllowedPlansResponse:
        """Retrieve a list of allowed plans for the current user."""
        parameters = {}
        response = self.get_implementation("/plans/allowed", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = AllowedPlansResponse.model_validate(response.json())
        return ret

    def devices_allowed(self) -> AllowedDevicesResponse:
        """Retrieve a list of allowed devices for the current user."""
        parameters = {}
        response = self.get_implementation("/devices/allowed", **parameters)
        if response.status_code != 200:
            logger.error("Request has failed: %s", response.json())
            response.raise_for_status()
        ret = AllowedDevicesResponse.model_validate(response.json())
        return ret
