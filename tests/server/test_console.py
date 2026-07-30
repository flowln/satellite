from collections.abc import Generator
import contextlib
import logging
import os
import sys
import uuid

import pytest

from satellite.server.console import LogCentralizer, create_standard_stream_rerouters


@contextlib.contextmanager
def unique_logger(
    queue_name: str, log_centralizer: LogCentralizer, *, with_random_part: bool = True
) -> Generator[logging.Logger]:
    if with_random_part:
        random_part = str(uuid.uuid4())
        logger = logging.getLogger(f"satellite.{queue_name}.{random_part}")
    else:
        logger = logging.getLogger(f"satellite.{queue_name}")

        old_handlers = logger.handlers
        for handler in old_handlers:
            logger.removeHandler(handler)

    old_propagate = logger.propagate
    logger.propagate = False

    log_centralizer.add_handler_to_logger(
        queue_name,
        logger,
        logging.Formatter("%(levelname)s %(message)s"),
        level=logging.DEBUG,
    )

    logger.setLevel(logging.DEBUG)

    yield logger

    logger.propagate = old_propagate


@pytest.fixture
def centralizer() -> Generator[tuple[LogCentralizer, tuple[str, int]]]:
    hostname = "localhost"

    obj = LogCentralizer("centralizer - test", host=hostname, port=0)
    obj.serve()

    yield obj, (hostname, obj.get_server_port())

    obj.close()


@pytest.fixture
def queue_name() -> str:
    return "test"


def test_logging_handler(centralizer, queue_name):
    with unique_logger(queue_name, centralizer[0]) as logger:
        assert centralizer[0].lookup_queue(queue_name) == []
        logger.info("Line #1")
        assert centralizer[0].lookup_queue(queue_name) == ["INFO Line #1"]
        logger.error("Line #2")
        assert centralizer[0].lookup_queue(queue_name) == [
            "INFO Line #1",
            "ERROR Line #2",
        ]
        logger.debug("Line #3")
        assert centralizer[0].lookup_queue(queue_name) == [
            "INFO Line #1",
            "ERROR Line #2",
            "DEBUG Line #3",
        ]

        assert centralizer[0].lookup_queue(queue_name, start=1) == [
            "ERROR Line #2",
            "DEBUG Line #3",
        ]
        assert centralizer[0].lookup_queue(queue_name, end=-1) == [
            "INFO Line #1",
            "ERROR Line #2",
        ]


def test_logging_handler_multiple_queues(centralizer):
    queue_name = "test"
    with unique_logger(queue_name, centralizer[0]) as logger:
        assert centralizer[0].lookup_queue(queue_name) == []
        logger.info("Line #1")
        assert centralizer[0].lookup_queue(queue_name) == ["INFO Line #1"]
        logger.error("Line #2")
        assert centralizer[0].lookup_queue(queue_name) == [
            "INFO Line #1",
            "ERROR Line #2",
        ]
        logger.debug("Line #3")
        assert centralizer[0].lookup_queue(queue_name) == [
            "INFO Line #1",
            "ERROR Line #2",
            "DEBUG Line #3",
        ]

    other_queue_name = "test2"
    with unique_logger(other_queue_name, centralizer[0]) as logger:
        assert centralizer[0].lookup_queue(other_queue_name) == []
        logger.debug("Line #1")
        assert centralizer[0].lookup_queue(other_queue_name) == ["DEBUG Line #1"]
        logger.error("Line #2")
        assert centralizer[0].lookup_queue(other_queue_name) == [
            "DEBUG Line #1",
            "ERROR Line #2",
        ]
        logger.warning("Line #3")
        assert centralizer[0].lookup_queue(other_queue_name) == [
            "DEBUG Line #1",
            "ERROR Line #2",
            "WARNING Line #3",
        ]

    # Assert the first queue's console hasn't changed
    assert centralizer[0].lookup_queue(queue_name) == [
        "INFO Line #1",
        "ERROR Line #2",
        "DEBUG Line #3",
    ]


def _subprocess_logging_code(host, port):
    import logging
    import logging.handlers

    handler = logging.handlers.SocketHandler(host, port)
    logging.basicConfig(
        format="%(levelname)s %(message)s",
        level=logging.INFO,
        handlers=[handler],
        force=True,
    )

    logger = logging.getLogger("satellite.test.remote")

    logger.info("Line #1")
    logger.debug("Line #2")
    logger.error("Line #3")


def test_socket_handler(centralizer, queue_name):
    # Configure the logger that will receive the LogRecord locally
    with unique_logger("test", centralizer[0], with_random_part=False):
        assert centralizer[0].lookup_queue(queue_name) == []

        from multiprocessing import Process

        _proc = Process(target=_subprocess_logging_code, args=centralizer[1])
        _proc.start()
        _proc.join(timeout=2.5)

        assert centralizer[0].lookup_queue(queue_name) == [
            "INFO Line #1",
            "ERROR Line #3",
        ]


def test_pipe_log(centralizer, queue_name, tmp_path):
    # Configure the logger that will receive the LogRecord locally
    with unique_logger("test", centralizer[0], with_random_part=False):
        assert centralizer[0].lookup_queue(queue_name) == []

        subprocess_code_path = tmp_path / "print_in_proc.py"
        with open(subprocess_code_path, "w") as _file:
            _file.writelines(
                [
                    "import sys\n",
                    "print('Line #1')\n",
                    "print('Line #2')\n",
                    "print('Line #3', file=sys.stderr)\n",
                ]
            )

        out_pipe = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        err_pipe = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)

        _out, _err = create_standard_stream_rerouters("satellite.test.remote", out_pipe, err_pipe)

        from subprocess import Popen

        _proc = Popen([sys.executable, str(subprocess_code_path)], stdout=_out, stderr=_err)
        _proc.wait(timeout=2.5)

        got = centralizer[0].lookup_queue(queue_name)
        expected_a = [
            "INFO Line #1",
            "INFO Line #2",
            "ERROR Line #3",
        ]
        expected_b = [
            "ERROR Line #3",
            "INFO Line #1",
            "INFO Line #2",
        ]

        # NOTE: Since it's not deterministic which pipe will become available first,
        # to avoid adding an arbitrary timeout, we accept either valid end result.
        assert got == expected_a or got == expected_b
