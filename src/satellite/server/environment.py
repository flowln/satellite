import asyncio
from collections import namedtuple
from collections.abc import Callable
import enum
import faulthandler
import logging
import logging.config
import logging.handlers
from pathlib import Path
import re
import runpy
import signal
import sys
import threading
import time as ttime
from traceback import format_exception
from typing import Any, no_type_check

from bluesky import RunEngine
from bluesky.protocols import HasName
from bluesky.run_engine import (
    RunEngineResult,
    get_bluesky_event_loop as _get_bluesky_event_loop,
)
from bluesky.utils import RunEngineInterrupted, is_plan

from ..annotations import (
    DeviceAnnotation,
    PlanAnnotation,
    generate_annotation_for_device,
    generate_annotation_for_plan,
)
from ..models import HistoryItem, QueueItem
from .configuration import (
    ManagerConfiguration,
    load_manager_configuration,
)
from .ipc import IPCCommunicationPair, create_streams_from_event_loop


def get_bluesky_event_loop() -> asyncio.AbstractEventLoop:
    """Simple wrapper for bluesky's `get_bluesky_event_loop` function, also setting its debug mode on."""
    _loop = _get_bluesky_event_loop()
    _loop.set_debug(True)
    return _loop


HealthCheckStatus = namedtuple("HealthCheckStatus", ("status",))

CloseEnvironment = namedtuple("CloseEnvironment", ("uuid",))
CloseEnvironmentResult = namedtuple("CloseEnvironmentResult", ("uuid",))

RetrieveAllPlanAnnotations = namedtuple("RetrieveAllPlanAnnotations", ("uuid",))
RetrieveAllPlanAnnotationsResult = namedtuple("RetrieveAllPlanAnnotationsResult", ("uuid", "value"))

RetrieveAllDeviceAnnotations = namedtuple("RetrieveAllDeviceAnnotations", ("uuid",))
RetrieveAllDeviceAnnotationsResult = namedtuple("RetrieveAllDeviceAnnotationsResult", ("uuid", "value"))

RunProvidedItem = namedtuple("RunProvidedItem", ("uuid", "item"))
RunProvidedItemResponse = namedtuple("RunProvidedItemResponse", ("uuid"))

ProvidedItemFinished = namedtuple("ProvidedItemFinished", ("uuid", "item", "stop_queue"), defaults=(None, None, False))

PauseExecution = namedtuple("PauseExecution", ("uuid", "is_deferred"))
PauseExecutionResult = namedtuple("PauseExecutionResult", ("uuid", "succeeded", "fail_message"))
RunEnginePaused = namedtuple("RunEnginePaused", ())

ResumeExecution = namedtuple("ResumeExecution", ("uuid",))
ResumeExecutionResult = namedtuple("ResumeExecutionResult", ("uuid", "succeeded", "fail_message"))
RunEngineResumed = namedtuple("RunEngineResumed", ())

StopExecution = namedtuple("StopExecution", ("uuid",))
StopExecutionResult = namedtuple("StopExecutionResult", ("uuid", "succeeded", "fail_message"))

AbortExecution = namedtuple("AbortExecution", ("uuid",))
AbortExecutionResult = namedtuple("AbortExecutionResult", ("uuid", "succeeded", "fail_message"))

HaltExecution = namedtuple("HaltExecution", ("uuid",))
HaltExecutionResult = namedtuple("HaltExecutionResult", ("uuid", "succeeded", "fail_message"))


class HealthStatus(enum.IntEnum):
    """Current status of the environment."""

    Opening = enum.auto()
    Closing = enum.auto()
    Idle = enum.auto()
    Running = enum.auto()


VALID_STARTUP_FILE_PATTERN = re.compile(r"(\d){1,3}.+\.i?py")


class PollingThread(threading.Thread):
    """Thread class for periodically calling a function inside an asyncio EventLoop."""

    def __init__(self, target: Callable, *, polling_time: float = 0.1):
        super().__init__(name="Environment - Polling Thread", daemon=True)

        self._target = target
        self._polling_time = polling_time

        self._running = False

    def run(self):  # noqa
        self._running = True

        event_loop = asyncio.new_event_loop()
        while self._running:
            event_loop.run_until_complete(self._target())

            ttime.sleep(self._polling_time)

        event_loop.close()

    def close(self):
        """Stop polling and quit the thread."""
        self._running = False


class EnvironmentProcess:
    """Reference object for an environment of plan execution with Bluesky."""

    @no_type_check
    def __init__(
        self,
        queue_name: str,
        configuration: ManagerConfiguration,
        *,
        connection_host: str = "localhost",
        connection_port: int,
    ):
        self._queue_name = queue_name

        self._connection_host = connection_host
        self._connection_port = connection_port

        self._startup_directory_path: Path = configuration.startup.startup_directory
        self._startup_preamble_files: list[Path] = [
            Path(__file__).parent / "default_environment_preamble.py",
        ]

        self._environment_globals = {}

        self._run_engine: RunEngine | None = None
        self._current_history_item = None

        self._available_plan_annotations: dict[str, PlanAnnotation] = {}
        self._available_plans: dict[str, Callable] = {}
        self._available_device_annotations: dict[str, DeviceAnnotation] = {}
        self._available_devices: dict[str, object] = {}

        self._scheduled_futures = set()
        self._event_loop: asyncio.AbstractEventLoop = None

        self._poll_timer: PollingThread = None

    def run(self):  # noqa
        faulthandler.enable()

        self._logger = logging.getLogger(f"satellite.{self._queue_name}.environment")

        self._event_loop = asyncio.new_event_loop()
        self._event_loop.set_debug(True)
        self._event_loop.add_signal_handler(signal.SIGINT, self.interrupt_process)
        self._event_loop.set_exception_handler(self._handle_exception_in_event_loop)

        _ = self._event_loop.create_task(self._setup_environment())

        try:
            self._event_loop.run_forever()
        finally:
            self._conn.close()

            if self._poll_timer.is_alive():
                self._poll_timer.close()

            self._event_loop.close()

    async def _setup_environment(self):
        self._logger.debug("Setting up environment...")

        connection_loop, reader, writer = await create_streams_from_event_loop(
            self._connection_host, self._connection_port
        )
        self._conn = IPCCommunicationPair(reader, writer, loop=connection_loop)

        await self._conn.send_message(HealthCheckStatus(HealthStatus.Opening))

        try:
            self._populate_from_startup()
        except Exception as exc:
            await self._conn.send_message(HealthCheckStatus(HealthStatus.Closing))

            self._logger.error("Exception was raised while loading environment:", exc_info=exc)

            raise

        self._parse_objects_from_globals()

        self._poll_timer = PollingThread(self._poll_for_messages)
        self._poll_timer.start()

        await self._conn.send_message(HealthCheckStatus(HealthStatus.Idle))

    async def _poll_for_messages(self):
        """Periodically poll for new incoming messages."""
        enqueued_message = await self._conn.read_message()
        if enqueued_message is None:
            return

        self._logger.debug("Received new message from manager: %s", str(enqueued_message))

        match enqueued_message:
            case CloseEnvironment(uuid):
                await self._conn.send_message(HealthCheckStatus(HealthStatus.Closing))
                await self._conn.send_message(CloseEnvironmentResult(uuid))

                self.interrupt_process()
            case RetrieveAllPlanAnnotations(uuid):
                annotation = self._available_plan_annotations
                await self._conn.send_message(RetrieveAllPlanAnnotationsResult(uuid, annotation))
            case RetrieveAllDeviceAnnotations(uuid):
                annotations = self._available_device_annotations
                await self._conn.send_message(RetrieveAllDeviceAnnotationsResult(uuid, annotations))
            case RunProvidedItem(uuid, item):
                future = asyncio.run_coroutine_threadsafe(self._run_item(item), self._event_loop)

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)

                await self._conn.send_message(RunProvidedItemResponse(uuid))
            case PauseExecution(uuid, is_deferred):
                if self._run_engine is None:
                    msg = "Failed to pause plan execution: No run engine is available."
                    self._logger.error(msg)

                    await self._conn.send_message(PauseExecutionResult(uuid, False, msg))
                    return

                # Run in the bluesky event loop because we're stuck running the RunEngine
                future = asyncio.run_coroutine_threadsafe(
                    self._run_engine._request_pause_coro(is_deferred),  # noqa: SLF001
                    get_bluesky_event_loop(),
                )

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)
                future.add_done_callback(
                    lambda _fut: asyncio.run_coroutine_threadsafe(
                        self._conn.send_message(RunEnginePaused()), self._event_loop
                    )
                )

                await self._conn.send_message(PauseExecutionResult(uuid, True, None))
            case ResumeExecution(uuid):
                if self._run_engine is None:
                    raise RuntimeError

                if self._run_engine.state != "paused":
                    msg = "Failed to resume plan execution: The run engine is not currently paused."
                    self._logger.error(msg)

                    await self._conn.send_message(ResumeExecutionResult(uuid, False, msg))
                    return

                future = asyncio.run_coroutine_threadsafe(self._resume_plan(), self._event_loop)

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)

                await self._conn.send_message(ResumeExecutionResult(uuid, True, None))
            case StopExecution(uuid):
                if self._run_engine is None:
                    msg = "Failed to stop plan execution: No run engine is available."
                    self._logger.error(msg)

                    await self._conn.send_message(StopExecutionResult(uuid, False, msg))
                    return

                if self._run_engine.state != "paused":
                    msg = "Failed to stop plan execution: The run engine is not currently paused."
                    self._logger.error(msg)

                    await self._conn.send_message(StopExecutionResult(uuid, False, msg))
                    return

                future = asyncio.run_coroutine_threadsafe(self._stop_plan(), self._event_loop)

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)

                await self._conn.send_message(StopExecutionResult(uuid, True, None))
            case AbortExecution(uuid):
                if self._run_engine is None:
                    msg = "Failed to abort plan execution: No run engine is available."
                    self._logger.error(msg)

                    await self._conn.send_message(AbortExecutionResult(uuid, False, msg))
                    return

                if self._run_engine.state != "paused":
                    msg = "Failed to abort plan execution: The run engine is not currently paused."
                    self._logger.error(msg)

                    await self._conn.send_message(AbortExecutionResult(uuid, False, msg))
                    return

                future = asyncio.run_coroutine_threadsafe(self._stop_plan(abort=True), self._event_loop)

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)

                await self._conn.send_message(AbortExecutionResult(uuid, True, None))
            case HaltExecution(uuid):
                if self._run_engine is None:
                    msg = "Failed to halt plan execution: No run engine is available."
                    self._logger.error(msg)

                    await self._conn.send_message(HaltExecutionResult(uuid, False, msg))
                    return

                if self._run_engine.state != "paused":
                    msg = "Failed to halt plan execution: The run engine is not currently paused."
                    self._logger.error(msg)

                    await self._conn.send_message(HaltExecutionResult(uuid, False, msg))
                    return

                future = asyncio.run_coroutine_threadsafe(self._stop_plan(halt=True), self._event_loop)

                self._scheduled_futures.add(future)
                future.add_done_callback(self._scheduled_futures.discard)

                await self._conn.send_message(HaltExecutionResult(uuid, True, None))

    def _handle_exception_in_event_loop(self, _loop: asyncio.AbstractEventLoop, context: dict[str, Any]):
        """
        Handle exceptions ocurring in the asyncio event loop.

        Reference: https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.call_exception_handler
        """
        self._logger.error(f"Error ocurred in event loop: {context.get('message', 'None')}")

        if "exception" in context:
            self._logger.exception(context["exception"])

    async def _run_item(self, item: QueueItem):
        """Run the provided QueueItem in the RunEngine."""
        match item.type:
            case "plan":
                if self._run_engine is None:
                    self._logger.error("No run engine is present in the environment. Failed to run plan.")

                    return

                await self._resume_plan(new_plan_item=item)
            case "instruction":
                history_item = HistoryItem.from_queue_item(item)
                match item.name:
                    case "queue_stop":
                        await self._conn.send_message(ProvidedItemFinished(history_item.uid, history_item, True))
                    case unrecognized:
                        self._logger.error("Unrecognized item type '%s'. Ignoring it.", unrecognized)

                        await self._conn.send_message(ProvidedItemFinished(history_item.uid, history_item))

    async def _resume_plan(self, *, new_plan_item: QueueItem | None = None):
        if self._run_engine is None:
            raise RuntimeError

        if new_plan_item is not None:
            args = new_plan_item.args

            args_str = ", ".join(args)
            kwargs_str = ", ".join(f"{k}={v}" for k, v in new_plan_item.kwargs.items())

            param_str = ""
            if len(args) == 0:
                param_str = kwargs_str
            if len(new_plan_item.kwargs) == 0:
                param_str = args_str

            if len(args) == 0 and len(new_plan_item.kwargs) == 0:
                param_str = ""

            history_item = HistoryItem.from_queue_item(new_plan_item)
            history_item.time_start = ttime.time()
            self._current_history_item = history_item

        if self._current_history_item is None:
            self._logger.error("Trying to resume a plan when it's not possible (missing HistoryItem).")

            return

        try:
            if new_plan_item is not None:
                await self._conn.send_message(HealthCheckStatus(HealthStatus.Running))

                result: RunEngineResult = eval(
                    f"RE({new_plan_item.name}({param_str}))",
                    self._environment_globals | {"RE": self._run_engine},
                )
            else:
                await self._conn.send_message(RunEngineResumed())

                result: RunEngineResult = self._run_engine.resume()

            await self._handle_run_engine_result(result)
        except RunEngineInterrupted:
            if self._run_engine.state in {"pausing", "paused"}:
                return

            self._current_history_item.exit_status = "aborted"
        except Exception as exc:
            self._current_history_item.exit_status = "failed"

            self._current_history_item.msg = str(exc.args[0] if len(exc.args) > 0 else exc)
            self._current_history_item.traceback = "".join(format_exception(exc))

        await self._finish_off_current_plan()

    async def _stop_plan(self, *, abort: bool = False, halt: bool = False):
        self._logger.debug(f"Running _stop_plan with args: {abort=} {halt=}")

        if self._run_engine is None:
            raise RuntimeError
        if self._current_history_item is None:
            raise RuntimeError

        if abort:
            future = asyncio.run_coroutine_threadsafe(
                self._run_engine._abort_coro("aborted by user request"),  # noqa: SLF001
                get_bluesky_event_loop(),
            )
        elif halt:
            future = asyncio.run_coroutine_threadsafe(self._run_engine._halt_coro(), get_bluesky_event_loop())  # noqa: SLF001
        else:
            future = asyncio.run_coroutine_threadsafe(self._run_engine._stop_coro(), get_bluesky_event_loop())  # noqa: SLF001

        result = future.result()

        await self._handle_run_engine_result(result)

        self._current_history_item.exit_status = "aborted" if abort else ("halted" if halt else "stopped")

        await self._finish_off_current_plan()

    async def _handle_run_engine_result(self, result: RunEngineResult):
        if self._current_history_item is None:
            self._logger.error("Trying to handle the run engine result without a cached HistoryItem (this is a bug!).")

            return

        self._current_history_item.run_uids = result.run_start_uids

        self._current_history_item.exit_status = result.exit_status.replace("success", "completed")

        self._current_history_item.msg = result.reason
        if result.exit_status != "success" and result.exception is not None and not isinstance(result.exception, type):
            self._current_history_item.traceback = "".join(format_exception(result.exception))

    async def _finish_off_current_plan(self):
        if self._current_history_item is None:
            self._logger.error("Trying to finish off the current plan without a cached HistoryItem (this is a bug!).")

            return

        self._current_history_item.time_stop = ttime.time()

        await self._conn.send_message(ProvidedItemFinished(self._current_history_item.uid, self._current_history_item))

        self._current_history_item = None

    def _populate_from_startup(self):
        """Run startup files and update the environment's globals."""
        if not self._startup_directory_path.exists():
            self._logger.error(
                "Failed to start environment: Startup directory '%s' does not exist.",
                self._startup_directory_path,
            )
            raise Exception

        if not self._startup_directory_path.is_dir():
            self._logger.error(
                "Failed to start environment: Startup directory '%s' is not a directory.",
                self._startup_directory_path,
            )
            raise Exception

        # First add the preamble files
        files_to_load = self._startup_preamble_files

        _files_to_add = []
        for path in self._startup_directory_path.iterdir():
            if not path.is_file():
                continue

            file_ordering = VALID_STARTUP_FILE_PATTERN.match(path.name)
            if file_ordering is None:
                continue

            _files_to_add.append((file_ordering.string, path))

        # Then add the startup files
        files_to_load.extend([x[1] for x in sorted(_files_to_add, key=lambda x: x[0])])

        for file_path in files_to_load:
            self._logger.info("Loading file '%s'...", str(file_path))

            self._environment_globals |= runpy.run_path(str(file_path), init_globals=self._environment_globals)

            # Ensure all output from the loaded file is sent.
            sys.stdout.flush()
            sys.stderr.flush()

        # Some sanity cleaning
        self._environment_globals = {k: v for k, v in self._environment_globals.items() if not k.startswith("__")}

    def _parse_objects_from_globals(self):
        """Parse the populated environment's globals for plans and devices in it."""
        for obj_name, obj in self._environment_globals.items():
            if is_plan(obj):
                self._available_plans[obj_name] = obj
                self._available_plan_annotations[obj_name] = generate_annotation_for_plan(obj, obj_name)
            elif isinstance(obj, HasName):
                self._available_devices[obj_name] = obj
                self._available_device_annotations[obj_name] = generate_annotation_for_device(obj, obj_name)
            elif isinstance(obj, RunEngine):
                self._run_engine = obj

                # NOTE: Override the user definition so we get more direct information.
                obj._call_returns_result = True  # noqa

    def interrupt_process(self):
        """Safely interrupt all processing being made by this object."""
        self._poll_timer.close()
        self._event_loop.call_soon_threadsafe(self._event_loop.stop)


def entrypoint():  # noqa
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("queue_name", type=str)
    parser.add_argument("port_number", type=int)

    args = parser.parse_args()

    global_configuration = load_manager_configuration()
    configuration = global_configuration.managers.get(args.queue_name, global_configuration)

    logging.config.dictConfig(
        {
            "version": 1,
            "formatters": {
                "basic": {
                    "format": "[%(asctime)s %(levelname)s - %(name)s] %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.handlers.SocketHandler",
                    "formatter": "basic",
                    "level": "DEBUG",
                    "host": "localhost",
                    "port": logging.handlers.DEFAULT_TCP_LOGGING_PORT,
                },
            },
            "loggers": {
                "": {
                    "handlers": ["console"],
                    "level": logging.INFO,
                },
                "satellite": {
                    "handlers": ["console"],
                    "level": logging.DEBUG,
                    "propagate": False,
                },
            },
        }
    )

    process = EnvironmentProcess(args.queue_name, configuration, connection_port=args.port_number)

    try:
        _ret = process.run()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()

    sys.exit(_ret)


if __name__ == "__main__":
    entrypoint()
