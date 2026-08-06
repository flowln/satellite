from collections import defaultdict
from collections.abc import Callable, Sequence
import copy
import logging
import logging.handlers
import os
import pickle
import select
import socketserver
import struct
import sys
import threading
from typing import IO, Self
from uuid import UUID
import weakref

from satellite.models import create_uuid

logger = logging.getLogger("satellite.server.console")


class _Buffer:
    def __init__(self):
        self._internal_buffer: dict[str, list] = defaultdict(list)
        self._individual_uids: dict[str, UUID] = defaultdict(create_uuid)
        self._uid_positions: dict[str, dict[UUID, int]] = defaultdict(dict)

    def append_to_queue(self, queue_name: str, line: str):
        self._internal_buffer[queue_name].append(line)
        self._individual_uids[queue_name] = create_uuid()

    def lookup_queue(self, queue_name: str, start: int = 0, end: int | None = None) -> list[str]:
        buffer = self._internal_buffer[queue_name]

        if start < 0:
            start = len(buffer) + start
        start = max(start, 0)

        # Properly return nothing if we're requesting from the very end
        if start >= len(buffer):
            return []

        if end is None:
            return buffer[start:]
        return buffer[start:end]

    def lookup_queue_by_uid(self, queue_name: str, start_uid: UUID, limit: int | None = None) -> list[str] | None:
        position = self._uid_positions[queue_name].get(start_uid)
        if position is None:
            return None

        if limit is not None:
            end_position = position + limit
        else:
            end_position = None

        return self.lookup_queue(queue_name, position, end_position)

    def get_uid_for_queue(self, queue_name: str) -> UUID:
        current_uid = self._individual_uids[queue_name]
        # NOTE: Next time, if looking up by uid, exclude this message from the results too.
        current_buffer_position = len(self._internal_buffer[queue_name])

        # NOTE: We only need to keep track of UIDs that are returned by this call,
        # since otherwise the user has no way of knowing any UIDs.
        self._uid_positions[queue_name][current_uid] = max(current_buffer_position, 0)

        return current_uid


class _LogCentralizerHandler(logging.Handler):
    """Handler for the logging module that sends log lines to an external buffer."""

    def __init__(
        self,
        *args,
        queue_name: str,
        owner: weakref.ProxyType,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self._queue_name = queue_name
        self._owner = owner

    @property
    def _buffer(self):
        try:
            return self._owner._buffer  # noqa
        except ReferenceError:
            print(
                "[ERROR] Logging handler called when no owner is alive.",
                file=sys.__stderr__,
            )

    def emit(self, record: logging.LogRecord):
        line = self.format(record)

        self._buffer.append_to_queue(self._queue_name, line)

    def get_owner(self) -> weakref.ProxyType:
        return self._owner

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        level_name = logging.getLevelName(self.level)

        return f"{class_name}(queue_name={self._queue_name}, owner={self._owner}, level={level_name})"


class _LogRecordStreamHandler(socketserver.StreamRequestHandler):
    """Handler for receiving remote log lines from a socket."""

    def handle(self):
        """
        Handle incoming data from the connection socket of the server.

        Each request is expected to have a 4-byte preamble telling the length
        of the stream, followed by the LogRecord in pickle format.
        """
        while True:
            chunk = self.connection.recv(4)
            if len(chunk) < 4:
                break

            record_length = struct.unpack(">L", chunk)[0]

            chunk = self.connection.recv(record_length)
            while len(chunk) < record_length:
                chunk += self.connection.recv(record_length - len(chunk))

            obj = pickle.loads(chunk)
            record = logging.makeLogRecord(obj)

            self._handle_log_record(record)

    def _handle_log_record(self, record):
        name = record.name

        logger = logging.getLogger(name)
        logger.handle(record)


class LogCentralizer:
    """
    Object responsible for centralizing logs from many sources into a single in-memory buffer, for later retrieval.

    There are two main ways to ingest log lines into this object:

    1. Through the logging module, using the handler returned by the `get_logging_handler` method.

    2. Through a socket connection. On the constructor, a host and a port are provided, which are used to
    create a TCP socket server which is served in another thread when executing the 'serve' method.
    External clients can then connect to it (e.g. using a `SocketHandler` from the logging module) and send
    pickled log lines to it.

    Ingestion of the internal buffer can be done with the `lookup_queue` method, which does NOT consume
    the internal buffer.
    """

    def __init__(
        self,
        name: str,
        host: str = "localhost",
        port: int = logging.handlers.DEFAULT_TCP_LOGGING_PORT,
    ):
        self._buffer = _Buffer()

        self._internal_socket_server = socketserver.TCPServer(
            (host, port), _LogRecordStreamHandler, bind_and_activate=False
        )
        self._internal_socket_server_name = name
        self._internal_socket_server_thread = None

    def lookup_queue(self, queue_name: str, start: int = 0, end: int | None = None) -> list[str]:
        """Fetch a subsection of the console logs from a queue."""
        return self._buffer.lookup_queue(queue_name, start, end)

    def lookup_queue_by_uid(self, queue_name: str, start_uid: UUID, limit: int | None = None) -> list[str] | None:
        """Fetch a subsection of the console logs from a queue."""
        return self._buffer.lookup_queue_by_uid(queue_name, start_uid, limit)

    def get_uid_for_queue(self, queue_name: str) -> UUID:
        """Return the UID associated with the current queue's console state."""
        return self._buffer.get_uid_for_queue(queue_name)

    def serve(self):
        """Spawn a new thread for processing incoming logs via TCP."""
        self._internal_socket_server.allow_reuse_address = True
        self._internal_socket_server.allow_reuse_port = True

        try:
            self._internal_socket_server.server_bind()
            self._internal_socket_server.server_activate()
        except Exception as exc:
            logger.error("Failed to start bind and activate socket server.", exc_info=exc)

            self._internal_socket_server.server_close()
            return

        self._internal_socket_server_thread = threading.Thread(
            target=self._internal_socket_server.serve_forever,
            name=self._internal_socket_server_name,
            kwargs={"poll_interval": 0.033},
            daemon=True,
        )
        self._internal_socket_server_thread.start()

    def add_handler_to_logger(
        self,
        queue_name: str,
        logger: logging.Logger,
        formatter: logging.Formatter | None = None,
        *,
        level: int = logging.INFO,
    ):
        """Add a handler to an existing logger for sending the logged data to the queue's console buffer."""
        owner = weakref.proxy(self)

        handler = _LogCentralizerHandler(queue_name=queue_name, owner=owner)
        handler.setLevel(level)

        if formatter is not None:
            handler.setFormatter(formatter)

        def _finalize():
            logger.removeHandler(handler)
            handler.close()

        weakref.finalize(self, _finalize)

        logger.addHandler(handler)

    def get_server_port(self) -> int:
        """Return the port associated with the currently running socket server."""
        return self._internal_socket_server.socket.getsockname()[1]

    def close(self):
        """Stop serving connections with the socket server, closing the thread and the associated resources."""
        if self._internal_socket_server_thread is not None:
            self._internal_socket_server.shutdown()
            self._internal_socket_server.server_close()

    @property
    def serving(self) -> bool:
        """Whether a socket server is currently being serviced."""
        return self._internal_socket_server_thread is not None and self._internal_socket_server_thread.is_alive()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(serving={self.serving})"


class PipeLogConsumer(threading.Thread, IO):
    """
    Consume log lines from a `os.pipe`, through the reading end.

    To use it, create an object of it, and then start the thread with the
    `start` method to open the reading file descriptor and start waiting for new lines.

    This object can be passed to `subprocess.Popen` objects via the `stdout`
    and `stderr` arguments, using the `with_fileno` method to get a shallow copy that
    returns the corresponding fileno, for lower-level operations on it.

    Parameters
    ----------
    fds : sequence of tuples of ints
        Tuples of (read, write) pairs of file descriptors pointing to the respective ends of a pipe.
    on_write : callable
        Callback to call whenever a new line has been received through the pipe.
        It takes in a str object with the line information on it.
    **kwargs : dict
        Extra keyword arguments to the `threading.Thread` superclass.

    """

    def __init__(
        self,
        fds: Sequence[tuple[int, int]],
        on_write: Callable[
            [
                int,
                str,
            ],
            None,
        ],
        **kwargs,
    ):
        super().__init__(daemon=True, **kwargs)

        self._read_fds = []
        self._write_fds = []

        for read_fd, write_fd in fds:
            self._read_fds.append(read_fd)
            self._write_fds.append(write_fd)

        self._on_write = on_write

        self._fileno = self._write_fds[0]
        self._closed = False

    def run(self):  # noqa: D102
        open_fds = {_fd: open(_fd) for _fd in self._read_fds}

        while not self._closed:
            try:
                rlist, _, _ = select.select(self._read_fds, [], [])
            except OSError:
                break

            for fd in rlist:
                _file = open_fds[fd]

                for line in iter(_file.readline, ""):
                    self._on_write(fd, line)

        logger.debug("Closing file descriptors associated with logging pipes...")

        if not self._closed:
            self.close()

    def with_fileno(self, index: int) -> Self:
        """Return a copy of this object, with a different output for the 'fileno' method."""
        if index == 0:
            return self

        _s = copy.copy(self)
        _s._fileno = self._write_fds[index]  # noqa
        return _s

    def fileno(self) -> int:
        """Return the file descriptor associated with this object, for lower-level IO."""
        return self._fileno

    def close(self):
        """Stop IO operations and clean up resources."""
        self._closed = True

        for write_fd in self._write_fds:
            os.close(write_fd)


def create_standard_stream_rerouters(
    logger_name: str,
    out_pipe: tuple[int, int],
    err_pipe: tuple[int, int],
    out_level: int = logging.INFO,
    err_level: int = logging.ERROR,
) -> tuple[PipeLogConsumer, PipeLogConsumer]:
    """Create objects to route the standard streams (stdout, stderr) to a logger from the logging module."""
    logger = logging.getLogger(logger_name)

    def _on_write(fd: int, data: str):
        level = out_level if fd == out_pipe[0] else err_level
        logger.log(level, data.strip("\n"))

    consumer = PipeLogConsumer([out_pipe, err_pipe], on_write=_on_write, name="Stream rerouter")
    stdout = consumer.with_fileno(0)
    stderr = consumer.with_fileno(1)

    consumer.start()

    return stdout, stderr


_LOG_CENTRALIZER = None


def get_global_log_centralizer(*args, **kwargs) -> LogCentralizer:
    """
    Use a LogCentralizer instance as a singleton.

    If a global instance does not yet exist, one is created with the arguments provided.
    """
    global _LOG_CENTRALIZER

    if _LOG_CENTRALIZER is None:
        _LOG_CENTRALIZER = LogCentralizer(*args, **kwargs)

    return _LOG_CENTRALIZER


def close_global_log_centralizer():
    """Close the global log centralizer and clean up the singleton instance."""
    global _LOG_CENTRALIZER

    if _LOG_CENTRALIZER is not None:
        _LOG_CENTRALIZER.close()

    _LOG_CENTRALIZER = None
