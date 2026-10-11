from abc import abstractmethod
import asyncio
from collections.abc import Callable
from enum import Enum, auto
import logging
import threading
import weakref

import httpx

logger = logging.getLogger("satellite.client.concurrent")


class WatchMethod(Enum):
    """Implementation method for watching for changes in remote states."""

    POLLING = auto()


class _StateWatcher:
    def __init__(
        self,
        client: weakref.ReferenceType[httpx.Client | httpx.AsyncClient],
        method: WatchMethod,
        *,
        polling_time: float | None = None,
    ):
        """
        Base implementation for monitoring state changes on the remote server in a worker thread.

        Parameters
        ----------
        client : weakref to httpx client
            The base client object to use when making network operations.
        method : WatchMethod
            The implementation method to be used for monitoring changes.
        polling_time : float, optional
            Time between sucessive polls to the server API, in seconds.
            Used in the 'POLLING' method, ignored otherwise.
        """
        self._last_received_data = None
        self._last_received_uid = None
        self._subscription_callbacks = []

        self._finish_requested = False

        self._client_ref = client
        self._polling_time = polling_time if polling_time is not None else 1.0

        match method:
            case WatchMethod.POLLING:
                self._handling_method = self._run_for_polling()
            case _method:
                raise NotImplementedError(f"The watching method '{_method}' is not implemented for StatusWatcher.")

        self._loop = asyncio.new_event_loop()

        self._task = self._loop.create_task(self._handling_method, name="StatusWatcher")
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="StatusWatcher - Helper Thread", daemon=True
        )

    def start(self):
        """Start monitoring changes in a separate thread. This can only be called once per object."""
        self._finish_requested = False

        self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Stop monitoring changes, and close the worker thread."""
        self._finish_requested = True

        if self._task is not None:
            self._task.cancel()
        if self._loop is not None:
            self._loop.stop()

        self._thread.join(timeout=timeout)
        return self._thread.is_alive()

    def add_callback(self, callback: Callable[[dict], None], *, call_now: bool = True):
        """Add a user-defined function to be called when the relevant state has changed."""
        self._subscription_callbacks.append(callback)

        if call_now and self._last_received_data is not None:
            callback(self._last_received_data)

    def remove_callback(self, callback: Callable[[dict], None]):
        """Remove a user-defined function from being called when the relevant state has changed."""
        self._subscription_callbacks.remove(callback)

    @abstractmethod
    def _endpoint_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _uid_response_field(self) -> str:
        raise NotImplementedError

    async def _run_for_polling(self):
        while not self._finish_requested:
            await asyncio.sleep(self._polling_time)

            client = self._client_ref()
            if client is None:
                logger.warning("The httpx client object was deleted before '%s' has stopped.", self.__class__.__name__)

                break

            if isinstance(client, httpx.Client):
                response = client.get(self._endpoint_name())
            elif isinstance(client, httpx.AsyncClient):
                response = await client.get(self._endpoint_name())
            else:
                logger.error(
                    "Failed to retrieve updated data when pooling: Invalid 'client' instance (%s).", type(client)
                )

                continue

            if response.status_code != 200:
                logger.error(
                    "Failed to retrieve updated data from '%s' when pooling: Failed request (code %d) -> %s",
                    self._endpoint_name(),
                    response.status_code,
                    str(response.json()),
                )

                continue

            self._last_received_data = response.json()
            last_received_uid = self._last_received_data.get(self._uid_response_field())

            if last_received_uid is None:
                logger.warning(
                    "Pooled response data does not contain a unique identifier (%s): %s",
                    self._uid_response_field(),
                    str(self._last_received_data),
                )

            if last_received_uid != self._last_received_uid:
                logger.debug(
                    "%s: Received new state change, propagating to %s subscriber(s)...",
                    self.__class__.__name__,
                    len(self._subscription_callbacks),
                )

                self._last_received_uid = last_received_uid

                for callback in self._subscription_callbacks:
                    callback(self._last_received_data)


class StatusWatcher(_StateWatcher):
    """Threaded watcher object for monitoring changes in the global 'status' state on the remote server."""

    def _endpoint_name(self) -> str:
        return "/status"

    def _uid_response_field(self) -> str:
        return "status_uid"
