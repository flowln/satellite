from abc import abstractmethod
import asyncio
from collections.abc import Callable, Coroutine
from enum import Enum, auto
import logging
import threading
import weakref

import httpx
from httpx_sse import aconnect_sse, connect_sse

logger = logging.getLogger("satellite.client.watchers")


class WatchMethod(Enum):
    """Implementation method for watching for changes in remote states."""

    POLLING = auto()
    SERVER_SENT_EVENTS = auto()


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
            case WatchMethod.SERVER_SENT_EVENTS:
                self._handling_method = self._run_for_server_sent_events()
            case _method:
                raise NotImplementedError(f"The watching method '{_method}' is not implemented for StatusWatcher.")

        self._loop = asyncio.new_event_loop()

        self._task = self._loop.create_task(self._run(self._handling_method), name="StatusWatcher")
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

        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)
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
    def _polling_endpoint_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _sse_endpoint_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _uid_response_field(self) -> str:
        raise NotImplementedError

    def _handle_received_state_object(self, state: dict):
        """Update internal data and subscriptions with the new data, if necessary."""
        self._last_received_data = state
        last_received_uid = self._last_received_data.get(self._uid_response_field())

        if last_received_uid is None:
            logger.warning(
                "%s: Response data does not contain a unique identifier (%s): %s",
                self.__class__.__name__,
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
        else:
            logger.debug(
                "%s: Received new state, but with the same UID as a previous received state.",
                self.__class__.__name__,
            )

    async def _run(self, handling_coro: Coroutine):
        try:
            await handling_coro
        except Exception as exc:
            logger.exception("%s: Exception raised during execution.", self.__class__.__name__, exc_info=exc)
        finally:
            logger.debug("%s: Exiting thread loop...", self.__class__.__name__)

    async def _run_for_polling(self):
        while not self._finish_requested:
            await asyncio.sleep(self._polling_time)

            client = self._client_ref()
            if client is None:
                logger.warning("The httpx client object was deleted before '%s' has stopped.", self.__class__.__name__)

                break

            if isinstance(client, httpx.Client):
                response = client.get(self._polling_endpoint_name())
            elif isinstance(client, httpx.AsyncClient):
                response = await client.get(self._polling_endpoint_name())

            if response.status_code != 200:
                logger.error(
                    "Failed to retrieve updated data from '%s' when pooling: Failed request (code %d) -> %s",
                    self._polling_endpoint_name(),
                    response.status_code,
                    str(response.json()),
                )

                continue

            self._handle_received_state_object(response.json())

    async def _run_for_server_sent_events(self):
        async def _connection_method():
            """Generator for getting a streaming connection."""
            client = self._client_ref()

            if client is None:
                logger.warning("The httpx client object was deleted before '%s' has started.", self.__class__.__name__)

                yield

            if isinstance(client, httpx.Client):
                with connect_sse(client, "GET", self._sse_endpoint_name()) as event_source:
                    yield event_source
            elif isinstance(client, httpx.AsyncClient):
                async with aconnect_sse(client, "GET", self._sse_endpoint_name()) as event_source:
                    yield event_source

        async for event_source in _connection_method():
            if event_source is None:
                continue

            if event_source.response.status_code != 200:
                logger.error(
                    "Failed to retrieve updated data from '%s' via SSE: Failed request (code %d) -> %s",
                    self._sse_endpoint_name(),
                    event_source.response.status_code,
                    str(event_source.response.json()),
                )

                return

            while not self._finish_requested:
                async for event in event_source.aiter_sse():
                    self._handle_received_state_object(event.json())


class StatusWatcher(_StateWatcher):
    """Threaded watcher object for monitoring changes in the global 'status' state on the remote server."""

    def _polling_endpoint_name(self) -> str:
        return "/status"

    def _sse_endpoint_name(self) -> str:
        return "/status/stream"

    def _uid_response_field(self) -> str:
        return "status_uid"


class ConsoleWatcher(_StateWatcher):
    """Threaded watcher object for monitoring changes in the console output on the remote server."""

    def _polling_endpoint_name(self) -> str:
        if self._last_received_uid is not None:
            return f"/console_output_update?last_msg_uid={self._last_received_uid}"
        return "/console_output"

    def _sse_endpoint_name(self) -> str:
        return "/console_output/stream"

    def _uid_response_field(self) -> str:
        return "last_msg_uid"
