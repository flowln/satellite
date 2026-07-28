import asyncio
from multiprocessing import Process
import os
import signal

import pytest

from satellite.server.environment import HealthCheckStatus, HealthStatus
from satellite.server.ipc import (
    IPCCommunicationPair,
    create_server_from_event_loop,
    create_streams_from_event_loop,
)

ReceiverType = tuple[
    asyncio.AbstractEventLoop,
    asyncio.Server,
    asyncio.StreamReader,
    asyncio.StreamWriter,
]
ClientType = tuple[asyncio.AbstractEventLoop, asyncio.StreamReader, asyncio.StreamWriter]


def ping_client_process_code(server_port: int):
    async def _inner():
        _loop, reader, writer = await create_streams_from_event_loop("localhost", server_port)
        _pair = IPCCommunicationPair(reader, writer, loop=_loop)

        while True:
            try:
                message = await _pair.read_message(timeout=2.0)
                if message is None:
                    break

                await _pair.send_message(message)
            except KeyboardInterrupt:
                break

    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(_inner())
    finally:
        loop.close()


@pytest.fixture()
def client():
    loop = asyncio.new_event_loop()

    connected_reader: asyncio.StreamReader | None = None
    connected_writer: asyncio.StreamWriter | None = None

    async def create_server():
        async def on_connection(reader, writer):
            nonlocal connected_reader, connected_writer

            connected_reader = reader
            connected_writer = writer

            loop = asyncio.get_running_loop()
            loop.stop()

        connection_loop, server = await create_server_from_event_loop(
            on_connection, allowed_hosts=["localhost"], port=0
        )

        return connection_loop, server

    connection_loop, server = loop.run_until_complete(create_server())

    server_port = server.sockets[0].getsockname()[1]
    client_proc = Process(target=ping_client_process_code, args=(server_port,))
    client_proc.start()

    connection_loop.run_forever()

    assert connected_reader is not None
    assert connected_writer is not None

    _pair = IPCCommunicationPair(connected_reader, connected_writer, loop=connection_loop)

    try:
        yield _pair
    finally:
        assert client_proc.pid is not None
        os.kill(client_proc.pid, signal.SIGINT)

        client_proc.join(2.0)
        client_proc.close()

        _pair.close()
        server.close()
        loop.close()


@pytest.mark.parametrize(
    ("sent_message",),
    (
        ["123"],
        [{"complex": "object", 123: 456}],
        [HealthCheckStatus(HealthStatus.Idle)],
    ),
)
async def test_simple_exchange(client: IPCCommunicationPair, sent_message):
    await client.send_message(sent_message)

    recv_message = await client.read_message(timeout=2.0)

    assert recv_message == sent_message
