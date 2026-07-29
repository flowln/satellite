from typing import Literal
from uuid import UUID

import httpx

from satellite.models import (
    ConsoleUidResponse,
    GenericResponse,
    HistoryResponse,
    LatestConsoleResponse,
    ManagerStatus,
    QueueAddRemoveResponse,
    QueueItem,
    QueueResponse,
)


class BaseAsyncClient(httpx.AsyncClient):
    def __init__(self, server_address: httpx.URL | str, **kwargs):
        super().__init__(base_url=server_address, **kwargs)

    async def _get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def _post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def ping(self) -> dict[Literal["message"], Literal["pong"]]:
        """Test connectivity with the queue manager. Always responds 'pong'."""
        response = await self._get_implementation("/ping")
        response.raise_for_status()
        ret = response.json()
        return ret

    async def status(self) -> ManagerStatus:
        """Retrieve the current state of the manager."""
        response = await self._get_implementation("/status")
        response.raise_for_status()
        ret = ManagerStatus.model_validate(response.json())
        return ret

    async def environment_open(self, lock_key: str | None = None) -> GenericResponse:
        """
        Open a new environment for plan execution.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        return await self._post_implementation("/environment_open", lock_key=lock_key)

    async def environment_close(self, lock_key: str | None = None) -> GenericResponse:
        """
        Close the currently active environment.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        return await self._post_implementation("/environment_close", lock_key=lock_key)

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
        return await self._post_implementation("/environment_destroy", lock_key=lock_key)

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
        response = await self._get_implementation("/history_get", limit=limit, offset=offset)
        response.raise_for_status()
        ret = HistoryResponse.model_validate(response.json())
        return ret

    async def history_clear(self) -> GenericResponse:
        """Clear the history of previously ran plans."""
        return await self._post_implementation("/history_clear")

    async def queue_get(self) -> QueueResponse:
        """Retrieve a list of all items currently in the queue."""
        response = await self._get_implementation("/queue_get")
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
        return await self._post_implementation("/queue_clear", lock_key=lock_key)

    async def queue_item_add(
        self,
        item: QueueItem,
        user_group: str = "primary",
        user: str = "default",
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
        user_group : str, optional
            The group associated with the user currently making the request. Defaults to 'primary'.

            It is used for recording information in the item, so that it's easier to track later.
        user : str, optional
            The user making the request. Defaults to 'default'.

            It is used for recording information in the item, so that it's easier to track later.
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
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation(
            "/queue_item_add",
            item=item,
            user_group=user_group,
            user=user,
            pos=pos,
            before_uid=before_uid,
            after_uid=after_uid,
            lock_key=lock_key,
        )

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
        return await self._post_implementation("/queue_item_remove", pos=pos, uid=uid, lock_key=lock_key)

    async def queue_start(self, lock_key: str | None = None) -> GenericResponse:
        """
        Start execution of the items in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation("/queue_start", lock_key=lock_key)

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
        return await self._post_implementation("/queue_stop", lock_key=lock_key)

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
        return await self._post_implementation("/queue_stop_cancel", lock_key=lock_key)

    async def run_engine_pause(
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
        return await self._post_implementation("/re_pause", option=option, lock_key=lock_key)

    async def run_engine_resume(self, lock_key: str | None = None) -> GenericResponse:
        """
        Resume execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation("/re_resume", lock_key=lock_key)

    async def run_engine_stop(self, lock_key: str | None = None) -> GenericResponse:
        """
        Stop execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation("/re_stop", lock_key=lock_key)

    async def run_engine_abort(self, lock_key: str | None = None) -> GenericResponse:
        """
        Abort execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation("/re_abort", lock_key=lock_key)

    async def run_engine_halt(self, lock_key: str | None = None) -> GenericResponse:
        """
        Halt execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        return await self._post_implementation("/re_halt", lock_key=lock_key)

    async def get_console_output(self, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output.

        Parameters
        ----------
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        response = await self._get_implementation("/console_output", lines=lines)
        response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    async def get_console_output_from_uid(self, last_msg_uid: UUID, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output, generated after some point.

        Parameters
        ----------
        last_msg_uid : UUID or str
            The uid (as returned by `/console_output/uid`) from which to start collecting lines.
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        response = await self._get_implementation("/console_output_update", last_msg_uid=last_msg_uid, lines=lines)
        response.raise_for_status()
        ret = LatestConsoleResponse.model_validate(response.json())
        return ret

    async def get_console_output_uid(self) -> ConsoleUidResponse:
        """
        Get a unique identifier for the current state of the console output.

        This identifier has the property that anytime a new line is appended to
        the console, a new uid is generated.
        """
        response = await self._get_implementation("/console_output/uid")
        response.raise_for_status()
        ret = ConsoleUidResponse.model_validate(response.json())
        return ret
