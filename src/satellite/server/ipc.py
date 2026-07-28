import asyncio
import base64
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
import logging
import pickle
import threading
import time as ttime
from typing import cast

logger = logging.getLogger("satellite.server.ipc")


async def create_streams_from_event_loop(
    host: str, port: int, **kwargs
) -> tuple[asyncio.AbstractEventLoop, asyncio.StreamReader, asyncio.StreamWriter]:
    """Create a pair of (reader, writer) stream objects connected to a remote server."""
    running_loop = asyncio.get_running_loop()
    connection_loop = asyncio.new_event_loop()

    reader, writer = await running_loop.run_in_executor(
        None,
        lambda: connection_loop.run_until_complete(asyncio.open_connection(host, port, **kwargs)),
    )

    return connection_loop, reader, writer


async def create_server_from_event_loop(
    callback: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None] | None],
    allowed_hosts: Sequence[str] | str,
    port: int,
    wait_for_connection: bool = False,
    connection_timeout: float = 2.0,
    **kwargs,
) -> tuple[asyncio.AbstractEventLoop, asyncio.Server]:
    """Create a server for accepting connections from remote clients and establish a two-way communication channel."""
    running_loop = asyncio.get_running_loop()
    connection_loop = asyncio.new_event_loop()

    server = await running_loop.run_in_executor(
        None,
        lambda: connection_loop.run_until_complete(asyncio.start_server(callback, allowed_hosts, port, **kwargs)),
    )

    if wait_for_connection:
        async with asyncio.timeout(connection_timeout):
            await running_loop.run_in_executor(None, lambda: connection_loop.run_forever())

    return connection_loop, server


class IPCCommunicationPair:
    """Associated pair of stream objects that handles communication with a remote client."""

    ENCODE_SEQUENCE: tuple[Callable, ...] = (pickle.dumps, base64.standard_b64encode)
    DECODE_SEQUENCE: tuple[Callable, ...] = (base64.standard_b64decode, pickle.loads)

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        """Helper object for managing an IPC connection with a remote client."""
        self._reader = reader
        self._writer = writer

        self._message_buffer_lock = threading.Lock()
        self._buffered_messages = deque()

        self._read_loop: asyncio.AbstractEventLoop | None = loop

        self._read_thread_handle = threading.Thread(target=self._read_socket_on_background, daemon=True)
        self._read_thread_handle.start()

    def _read_socket_on_background(self):
        if self._read_loop is None:
            self._read_loop = asyncio.new_event_loop()

        _partial_message = b""
        while not self._reader.at_eof():
            while self._read_loop.is_running():
                continue

            try:
                _raw_data = _partial_message + self._read_loop.run_until_complete(self._reader.readline())
            except ConnectionResetError:
                break

            if len(_raw_data) == len(_partial_message):
                break

            if not _raw_data.endswith(b"\n"):
                _partial_message += _raw_data

                continue

            _partial_message = b""
            _raw_message = _raw_data.removesuffix(b"\n")
            try:
                _message = _raw_message

                for op in self.DECODE_SEQUENCE:
                    _message = op(_message)
            except pickle.UnpicklingError as exc:
                logger.info("Failed to unpickle message from IPC socket.", exc_info=exc)

            with self._message_buffer_lock:
                self._buffered_messages.append(_message)
            logger.debug("Put message into read buffer: %s", str(type(_message).__name__))

        self._read_loop.close()

    def available_messages(self) -> int:
        """Return the number of available messages to read from the internal buffer."""
        return len(self._buffered_messages)

    async def read_message(self, timeout: float = 0.0) -> tuple | None:
        """
        Read a received message, taking it off the internal buffer.

        This function does not wait for a new message to come.

        Returns
        -------
        tuple
            A new message, as a namedtuple of the message type.
        None
            When no new messages have been received yet.

        """
        _initial_time = ttime.monotonic()
        while ttime.monotonic() - _initial_time < timeout:
            if self.available_messages() != 0:
                break

            await asyncio.sleep(timeout / 10.0)

        if self.available_messages() >= 1:
            with self._message_buffer_lock:
                message = self._buffered_messages.popleft()

                logger.debug("Took message from read buffer: %s", str(type(message).__name__))

                return message

    async def send_message(self, message: tuple) -> None:
        """
        Send a new message to the remote pair.

        Parameters
        ----------
        message : tuple
            The message to be encoded and sent.

        """
        try:
            _raw_data = message

            for op in self.ENCODE_SEQUENCE:
                _raw_data = op(_raw_data)
        except pickle.PicklingError as exc:
            logger.error("Failed to pickle message to IPC socket.", exc_info=exc)

            return

        logger.debug("Sending message through IPC: %s", str(type(message).__name__))

        self._writer.write(cast(bytes, _raw_data) + b"\n")
        await self._writer.drain()

    def close(self):
        """Close the streams associated with this object."""
        self._reader.feed_eof()
        self._writer.write_eof()
