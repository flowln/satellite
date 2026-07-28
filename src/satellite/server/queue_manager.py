import asyncio
import atexit
from collections.abc import Callable
from functools import partial, update_wrapper, wraps
import logging
import os
import subprocess
import time
from typing import Any, Literal, cast, no_type_check
from uuid import uuid4 as create_uuid

from fastapi import APIRouter

from satellite.server.configuration import ManagerConfiguration
from satellite.server.ipc import IPCCommunicationPair, create_server_from_event_loop
from satellite.server.persistence import create_backend_for_configuration

from ..annotations import (
    DeviceAnnotation,
    PlanAnnotation,
    ValidationError,
    validate_plan,
)
from ..models import (
    ConsoleUidResponse,
    GenericResponse,
    HistoryResponse,
    LatestConsoleResponse,
    ManagerStatus,
    QueueAddRemoveResponse,
    QueueItem,
    QueueResponse,
)
from .console import (
    create_standard_stream_rerouters,
    get_global_log_centralizer,
)
from .environment import (
    AbortExecution,
    AbortExecutionResult,
    CloseEnvironment,
    CloseEnvironmentResult,
    HaltExecution,
    HaltExecutionResult,
    HealthCheckStatus,
    HealthStatus,
    PauseExecution,
    PauseExecutionResult,
    ProvidedItemFinished,
    ResumeExecution,
    ResumeExecutionResult,
    RetrieveAllDeviceAnnotations,
    RetrieveAllDeviceAnnotationsResult,
    RetrieveAllPlanAnnotations,
    RetrieveAllPlanAnnotationsResult,
    RunEnginePaused,
    RunEngineResumed,
    RunProvidedItem,
    RunProvidedItemResponse,
    StopExecution,
    StopExecutionResult,
)

API_ROUTES: list[tuple[str, Callable, tuple, dict]] = []


def _endpoint(method: Literal["GET", "POST"], *w_args, **w_kwargs):
    def __wrapper(func: Callable):
        global API_ROUTES
        API_ROUTES.append((method.lower(), func, w_args, w_kwargs))

        def __inner(self, *args, **kwargs):
            return func(self, *args, **kwargs)

        update_wrapper(__inner, func)

        return __inner

    return __wrapper


@wraps(APIRouter.get)
def get_endpoint(*w_args, **w_kwargs):  # noqa
    return _endpoint("GET", *w_args, **w_kwargs)


@wraps(APIRouter.post)
def post_endpoint(*w_args, **w_kwargs):  # noqa
    return _endpoint("POST", *w_args, **w_kwargs)


class QueueManager:
    """
    A manager for a queue of plans and instructions.

    It manages the execution environment on its own, spawning a new process for handling plan execution.

    Aside from that, this manager also has HTTP endpoint definitions, and responds to them in order
    to enable communication with remote clients, applying operations as needed.
    """

    @no_type_check
    def __init__(self, name: str, configuration: ManagerConfiguration):
        self._name = name
        self._uuid = create_uuid()

        self._configuration = configuration

        self._logger = logging.getLogger(f"satellite.{name}.manager")
        self._logger.setLevel(self._configuration.operation.actual_logging_level)
        if not self._configuration.operation.print_console_output:
            self._logger.setLevel(-1)

        self._router: APIRouter | None = None

        self._persistence_backend = create_backend_for_configuration(self._name, self._configuration)

        self._environment_process_handle: subprocess.Popen | None = None
        self._environment_conn_server: tuple[asyncio.AbstractEventLoop, asyncio.Server] | None = None
        self._environment_conn: IPCCommunicationPair | None = None

        self._status = ManagerStatus(manager_uuid=self._uuid)
        self._status_initialized: bool = False

        atexit.register(self._on_exit)

        self._logger.info("Server started successfully!")

    @property
    def id(self) -> str:
        """Unique ID associated with this manager."""
        return str(self._uuid)

    def get_router(self) -> APIRouter:
        """Retrieve an APIRouter for interaction with this queue manager."""
        if self._router is not None:
            return self._router

        self._router = APIRouter()

        for method, func, args, kwargs in API_ROUTES:
            endpoint = partial(func, self)
            endpoint = update_wrapper(endpoint, func, assigned=("__doc__", "__name__", "__qualname__"))
            del endpoint.__wrapped__

            getattr(self._router, method)(*args, **kwargs)(endpoint)

        return self._router

    async def _check_for_enqueued_message(self, force_update_status: bool) -> Any:
        """
        Retrieve a new message from the environment, if available.

        Parameters
        ----------
        force_update_status : bool
            If True, change the manager state uid and modification time even when
            the actual state remains the same.

        Returns
        -------
        None
            If no new message has arrived.
        namedtuple
            If a new message has arrived, the message itself is returned.

        """
        if self._environment_conn is None:
            return

        if self._environment_conn.available_messages() == 0:
            return

        enqueued_message = await self._environment_conn.read_message()
        return_value = enqueued_message

        self._logger.debug("Received new message from environment: %s", str(enqueued_message))
        match enqueued_message:
            case CloseEnvironmentResult(_uuid):
                pass
            case HealthCheckStatus(HealthStatus.Opening):
                await self._status.update_manager_state("creating_environment", force=force_update_status)
                self._status.worker_environment_exists = True
                self._status.worker_environment_state = "initializing"
            case HealthCheckStatus(HealthStatus.Closing):
                await self._status.update_manager_state("closing_environment", force=force_update_status)
                self._status.worker_environment_state = "closing"
            case HealthCheckStatus(HealthStatus.Idle):
                await self._status.update_manager_state("idle", force=force_update_status)
                self._status.worker_environment_state = "idle"
            case HealthCheckStatus(HealthStatus.Running):
                await self._status.update_manager_state("executing_queue", force=force_update_status)
                self._status.worker_environment_state = "running"
            case RetrieveAllPlanAnnotationsResult(_uid, _value):
                pass
            case RetrieveAllDeviceAnnotationsResult(_uid, _value):
                pass
            case RunProvidedItemResponse(uuid):
                item = await self._persistence_backend.queue_pop_item()
                if item.uid != uuid:
                    self._logger.error(
                        "Running item with uid '%s', when the item with uid '%s' was expected.",
                        uuid,
                        item.uid,
                    )

                self._status.items_in_queue -= 1
                self._status.plan_queue_uid = create_uuid()
                self._status.running_item_uid = item.uid
            case ProvidedItemFinished(_uuid, item):
                self._status.running_item_uid = None

                await self._persistence_backend.history_insert_item(item, 0)

                self._status.plan_history_uid = create_uuid()
                self._status.items_in_history += 1

                if self._status.queue_stop_pending or self._status.items_in_queue == 0:
                    self._status.queue_stop_pending = False

                    await self._status.update_manager_state("idle", force=force_update_status)
                    self._status.worker_environment_state = "idle"

                    return

                response = await self._enqueue_next_item_for_running(ignore_manager_state=True)

                if not response.success:
                    self._logger.error("Failed to start next item in the queue: %s", response.msg)

                    await self._status.update_manager_state("idle", force=force_update_status)
                    self._status.worker_environment_state = "idle"

            case PauseExecutionResult(_uuid, _s, _m):
                self._status.worker_environment_state = "pausing"
            case RunEnginePaused():
                await self._status.update_manager_state("paused", force=force_update_status)
                self._status.worker_environment_state = "paused"
            case ResumeExecutionResult(_uuid, _s, _m):
                pass
            case RunEngineResumed():
                await self._status.update_manager_state("executing_queue", force=force_update_status)
                self._status.worker_environment_state = "running"
            case StopExecutionResult(_uuid, _s, _m):
                pass
            case AbortExecutionResult(_uuid, _s, _m):
                pass
            case HaltExecutionResult(_uuid, _s, _m):
                pass
            case unhandled:
                self._logger.warning("Unhandled object from environment sent: %s", repr(unhandled))

        return return_value

    async def check_environment_process(self, force_update_status: bool = False):
        """
        Update the current manager status with the state of the environment process.

        Parameters
        ----------
        force_update_status : bool
            If True, change the manager state uid and modification time even when
            the actual state remains the same.

        """
        if not self._status_initialized:
            self._status.items_in_queue = await self._persistence_backend.queue_length()
            self._status.items_in_history = await self._persistence_backend.history_length()

            self._status_initialized = True

        if self._environment_process_handle is None:
            await self._status.update_manager_state("idle", force=force_update_status)

            self._status.worker_environment_exists = False
            self._status.worker_environment_state = "closed"

        elif (return_code := self._environment_process_handle.poll()) is not None:
            if return_code != 0:
                self._logger.warning(
                    "The environment process has quit with exit code %d.",
                    return_code,
                )

            await self._status.update_manager_state("idle", force=force_update_status)

            self._status.worker_environment_exists = False
            self._status.worker_environment_state = "closed"

            self._environment_process_handle = None

            if self._environment_conn is not None:
                self._environment_conn.close()
                self._environment_conn = None

        while (await self._check_for_enqueued_message(force_update_status)) is not None:
            pass

    async def _ask_environment(self, req_class: type, rsp_class: type, *args, **kwargs) -> Any:
        """
        Ask the environment for something, and wait for it to respond.

        Parameters
        ----------
        req_class : type
            Type of the request to be made to the environment
        rsp_class : type
            Type of the response expected from the environment
        *args : Sequence[typing.Any]
            Arguments to construct the request object with.
        **kwargs : dict[str, typing.Any]
            Keyword arguments to construct the request object with.

        Returns
        -------
        None
            If the environment did not answer within 5.0 seconds.
        rsp_class object
            The response sent by the environment.

        """
        if self._environment_conn is None:
            self._logger.error("No connection to the environment has been established.")
            return

        message_uuid = create_uuid()

        message = req_class(message_uuid, *args, **kwargs)
        self._logger.debug("Sending message to environment: %s", str(message))
        await self._environment_conn.send_message(message)

        response = None

        _initial_time = time.time()
        while time.time() - _initial_time <= 5.0:
            return_value = await self._check_for_enqueued_message(False)

            if return_value is not None and isinstance(return_value, rsp_class) and return_value.uuid == message_uuid:
                response = return_value

                break

            await asyncio.sleep(0.025)

        return response

    async def _retrieve_annotation_for_plan(self, item: QueueItem) -> PlanAnnotation | None:
        # Retrieve cached value, it there is one.
        annotation = await self._persistence_backend.get_existing_plans(sub_key=item.name)
        if isinstance(annotation, PlanAnnotation):
            return annotation

        # Get current annotations if environment exists
        await self.check_environment_process()

        if self._environment_process_handle is None:
            self._logger.info("Failed to retrieve annotation for item '%s'.", item.name)

            return

        annotations = (await self._ask_environment(RetrieveAllPlanAnnotations, RetrieveAllPlanAnnotationsResult)).value

        # Cache the received value.
        await self._persistence_backend.set_existing_plans(annotations)

        return annotations.get(item.name)

    async def _retrieve_device_annotations(self) -> dict[str, DeviceAnnotation] | None:
        # Retrieve cached value, it there is one.
        annotations = await self._persistence_backend.get_existing_devices()
        if isinstance(annotations, dict) and len(annotations) != 0:
            return annotations

        # Get current annotations if environment exists
        await self.check_environment_process()

        if self._environment_process_handle is None:
            self._logger.info("Failed to retrieve device annotations from environment.")

            return

        annotations = (
            await self._ask_environment(RetrieveAllDeviceAnnotations, RetrieveAllDeviceAnnotationsResult)
        ).value

        # Cache the received value.
        await self._persistence_backend.set_existing_devices(annotations)

        return annotations

    async def _enqueue_next_item_for_running(self, *, ignore_manager_state: bool = False) -> GenericResponse:
        """
        Send the next item on the queue for execution.

        Parameters
        ----------
        ignore_manager_state : bool, optional
            Ignore whether the manager is currently in the 'idle' state.
            This is used for enqueuing a new item just after a previous one has finished.


        Returns
        -------
        GenericResponse
            A response of whether the operation has succeeded or not, and if not, why.

        """
        ret = GenericResponse()

        if not ignore_manager_state and self._status.manager_state != "idle":
            ret.success = False
            ret.msg = (
                f"Failed to start queue: Manager is not idling (currently in state '{self._status.manager_state}')."
            )
            return ret

        if self._environment_process_handle is None or self._environment_conn is None:
            ret.success = False
            ret.msg = "Failed to start queue: No environment is currently open."
            return ret

        if self._status.items_in_queue == 0:
            ret.success = False
            ret.msg = "Failed to start queue: Queue is currently empty."
            return ret

        _, item = await self._persistence_backend.queue_get_item(index=0)
        await self._environment_conn.send_message(RunProvidedItem(item.uid, item))

        return ret

    @get_endpoint("/ping")
    async def ping(self):
        """Test connectivity with the queue manager. Always responds 'pong'."""
        return {"message": "pong"}

    @get_endpoint("/status")
    async def status(self) -> ManagerStatus:
        """Retrieve the current state of the manager."""
        await self.check_environment_process()

        return self._status

    def _accept_environment_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Callback for a `asyncio.Server` instance when creating a new connection."""
        if self._environment_conn_server is None:
            raise RuntimeError

        self._environment_conn_server[0].stop()

        if self._environment_conn is not None:
            self._logger.error("Creating a new socket connection while there's already an existing one.")

            self._environment_conn.close()
        self._environment_conn = IPCCommunicationPair(reader, writer, loop=self._environment_conn_server[0])

    async def _wait_for_environment_connection(self, timeout: float = 5.0):
        if self._environment_conn is not None:
            return
        if self._environment_conn_server is None:
            raise RuntimeError

        async with asyncio.timeout(timeout):
            loop = asyncio.get_running_loop()
            connection_loop = self._environment_conn_server[0]

            await loop.run_in_executor(None, lambda: connection_loop.run_forever())

    @post_endpoint("/environment_open")
    async def environment_open(self, lock_key: str | None = None) -> GenericResponse:
        """
        Open a new environment for plan execution.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        if self._environment_process_handle is not None and self._environment_process_handle.poll() is None:
            ret.success = False
            ret.msg = "Could not open the environment, since it already exists."
            return ret

        if self._environment_conn_server is None:
            self._environment_conn_server = await create_server_from_event_loop(
                self._accept_environment_connection, "localhost", 0
            )

        port_number = self._environment_conn_server[1].sockets[0].getsockname()[1]
        self._logger.debug(
            "Creating environment connection server on host '%s' and port '%d'.",
            "localhost",
            port_number,
        )

        out_pipe = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        err_pipe = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        _out, _err = create_standard_stream_rerouters(f"satellite.{self._name}.environment.print", out_pipe, err_pipe)

        self._environment_process_handle = subprocess.Popen(
            [
                "satellite-server-environment",
                self._name,
                str(port_number),
            ],
            stdout=_out,
            stderr=_err,
        )

        try:
            await self._wait_for_environment_connection()
        except TimeoutError:
            ret.success = False
            ret.msg = "Failed to open environment: Timed out waiting for environment startup."
            return ret

        return ret

    @post_endpoint("/environment_close")
    async def environment_close(self, lock_key: str | None = None) -> GenericResponse:
        """
        Close the currently active environment.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._environment_process_handle is None:
            ret.success = False
            ret.msg = "Could not close the environment, since it is already closed."
            return ret

        await self._ask_environment(CloseEnvironment, CloseEnvironmentResult)
        await self.check_environment_process(force_update_status=True)

        return ret

    @post_endpoint("/environment_destroy")
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
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        if self._environment_process_handle is None:
            ret.success = False
            ret.msg = "Could not destroy the environment, since it is already closed."
            return ret

        self._environment_process_handle.terminate()

        await self.check_environment_process(force_update_status=True)

        return ret

    @get_endpoint("/history_get")
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
        await self.check_environment_process()

        success = True
        msg = ""

        if offset >= self._status.items_in_history:
            items_in_history = []

            success = False
            msg = f"The provided offset '{offset}' is outside the bounds of the current history."
        else:
            items_in_history = await self._persistence_backend.history_get(offset, limit)

        return HistoryResponse(
            items=items_in_history,
            plan_history_uid=self._status.plan_history_uid,
            success=success,
            msg=msg,
        )

    @post_endpoint("/history_clear")
    async def history_clear(self) -> GenericResponse:
        """Clear the history of previously ran plans."""
        ret = GenericResponse()

        await self._persistence_backend.history_clear()
        self._status.items_in_history = 0

        return ret

    @get_endpoint("/queue_get")
    async def queue_get(self) -> QueueResponse:
        """Retrieve a list of all items currently in the queue."""
        await self.check_environment_process()

        queue = await self._persistence_backend.queue_get()
        items_in_queue = [item.uid for item in queue if item.uid is not None]
        return QueueResponse(items=items_in_queue, plan_queue_uid=self._status.plan_queue_uid)

    @post_endpoint("/queue_clear")
    async def queue_clear(self, lock_key: str | None = None) -> GenericResponse:
        """
        Remove all items currently in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self._persistence_backend.queue_clear()
        self._status.items_in_queue = 0

        return ret

    @post_endpoint("/queue_item_add")
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
        ret = QueueAddRemoveResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        # Validate item
        if item.type == "plan":
            annotation = await self._retrieve_annotation_for_plan(item)

            if annotation is None:
                ret.success = False
                ret.msg = f"Failed to add item to queue: The specified plan '{item.name}' doesn't exist."
                return ret

            device_annotations = await self._retrieve_device_annotations()
            try:
                validate_plan(
                    annotation,
                    item.args,
                    item.kwargs,
                    device_annotations=device_annotations,
                )
            except ValidationError as exc:
                ret.success = False
                ret.msg = f"Failed to add item to queue: Validation failed - {str(exc)}"
                return ret

        # Populate information
        item.uid = create_uuid()
        item.metadata["user"] = user
        item.metadata["user_group"] = user_group

        # Insert in the queue at the correct position
        if before_uid is not None or after_uid is not None:
            uid = cast(str, before_uid or after_uid)

            try:
                position, _ = await self._persistence_backend.queue_get_item(uuid=uid)
            except Exception:
                ret.success = False
                ret.msg = f"Failed to add item to queue: No item with uid '{uid}' could be found."
                return ret

            if after_uid is not None:
                position += 1

            await self._persistence_backend.queue_insert_item(item, position)
        else:
            match pos:
                case "front":
                    await self._persistence_backend.queue_insert_item(item, 0)
                case "back":
                    await self._persistence_backend.queue_insert_item(item, None)
                case index:
                    if index >= self._status.items_in_queue:
                        ret.success = False
                        ret.msg = "Failed to add item to queue:"
                        f"Position '{index}' is outside the range allowed in the current queue."
                        return ret

                    await self._persistence_backend.queue_insert_item(item, index)

        # Prepare return
        self._status.plan_queue_uid = create_uuid()
        self._status.items_in_queue = await self._persistence_backend.queue_length()

        ret.queue_size = self._status.items_in_queue
        ret.item = item

        await self.check_environment_process(force_update_status=True)

        return ret

    @post_endpoint("/queue_item_remove")
    async def queue_item_remove(
        self,
        pos: int | Literal["front", "back"] | None = None,
        uid: str | None = None,
        lock_key: str | None = None,
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
        ret = QueueAddRemoveResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        # Remove from the queue at the correct position
        if pos is not None:
            match pos:
                case "front":
                    item = await self._persistence_backend.queue_pop_item(0)
                case "back":
                    item = await self._persistence_backend.queue_pop_item(-1)
                case index:
                    if index >= self._status.items_in_queue:
                        ret.success = False
                        ret.msg = "Failed to remove item from the queue:"
                        f"Position '{index}' is outside the range allowed in the current queue."
                        return ret

                    item = await self._persistence_backend.queue_pop_item(index)
        elif uid is not None:
            try:
                index, _ = await self._persistence_backend.queue_get_item(uuid=uid)
            except Exception as exc:
                self._logger.debug("Failed to remove item from the queue.", exc_info=exc)
                ret.success = False
                ret.msg = f"Failed to remove item from the queue: No item with uid '{uid}' could be found."
                return ret

            item = await self._persistence_backend.queue_pop_item(index)
        else:
            ret.success = False
            ret.msg = "Failed to remove item from the queue: Either 'pos' or 'uid' must be specified."
            return ret

        # Prepare return
        self._status.plan_queue_uid = create_uuid()
        self._status.items_in_queue = await self._persistence_backend.queue_length()

        ret.queue_size = self._status.items_in_queue
        ret.item = item

        await self.check_environment_process(force_update_status=True)

        return ret

    @post_endpoint("/queue_start")
    async def queue_start(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Start execution of the items in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        new_ret = await self._enqueue_next_item_for_running()
        if new_ret.msg == "":
            new_ret.msg = ret.msg

        return new_ret

    @post_endpoint("/queue_stop")
    async def queue_stop(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Stop execution of the items in the queue.

        This will mark the manager for stopping, which will be applied when the currently
        running item finishes executing (i.e. stop after the current item finishes).

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.manager_state == "idle":
            ret.success = False
            ret.msg = "Failed to stop queue: Manager is currently idling."
            return ret

        if self._environment_process_handle is None:
            ret.success = False
            ret.msg = "Failed to stop queue: No environment is currently open."
            return ret

        self._status.queue_stop_pending = True

        return ret

    @post_endpoint("/queue_stop_cancel")
    async def queue_stop_cancel(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Ensure the queue continues executing after the current item.

        This will clear the mark left by a previous call to `queue_stop`, so that
        it now continues execution after the current item finishes executing.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if not self._status.queue_stop_pending:
            self._logger.warning("Cancelled a queue stop while it was not scheduled for stopping.")
        self._status.queue_stop_pending = False

        return ret

    @post_endpoint("/re_pause")
    async def run_engine_pause(
        self,
        option: Literal["immediate", "deferred"] = "deferred",
        lock_key: str | None = None,
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
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.worker_environment_state != "running":
            ret.success = False
            ret.msg = "Failed to pause plan: No plan is currently running."

            return ret

        result: PauseExecutionResult = await self._ask_environment(
            PauseExecution, PauseExecutionResult, is_deferred=(option == "deferred")
        )

        ret.success = result.succeeded
        if not ret.success:
            ret.msg = result.fail_message

        return ret

    @post_endpoint("/re_resume")
    async def run_engine_resume(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Resume execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.worker_environment_state != "paused":
            ret.success = False
            ret.msg = "Failed to resume plan: No plan is currently paused."

            return ret

        result: ResumeExecutionResult = await self._ask_environment(ResumeExecution, ResumeExecutionResult)

        ret.success = result.succeeded
        if not ret.success:
            ret.msg = result.fail_message

        return ret

    @post_endpoint("/re_stop")
    async def run_engine_stop(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Stop execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.worker_environment_state != "paused":
            ret.success = False
            ret.msg = "Failed to stop plan: No plan is currently paused."

            return ret

        result: StopExecutionResult = await self._ask_environment(StopExecution, StopExecutionResult)

        ret.success = result.succeeded
        if not ret.success:
            ret.msg = result.fail_message

        return ret

    @post_endpoint("/re_abort")
    async def run_engine_abort(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Abort execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.worker_environment_state != "paused":
            ret.success = False
            ret.msg = "Failed to abort plan: No plan is currently paused."

            return ret

        result: AbortExecutionResult = await self._ask_environment(AbortExecution, AbortExecutionResult)

        ret.success = result.succeeded
        if not ret.success:
            ret.msg = result.fail_message

        return ret

    @post_endpoint("/re_halt")
    async def run_engine_halt(
        self,
        lock_key: str | None = None,
    ) -> GenericResponse:
        """
        Halt execution of a paused item.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        if lock_key is not None:
            ret.msg = "A non-null 'lock_key' was supplied, but support for it is not yet implemented. Ignoring it."

        await self.check_environment_process()

        if self._status.worker_environment_state != "paused":
            ret.success = False
            ret.msg = "Failed to halt plan: No plan is currently paused."

            return ret

        result: HaltExecutionResult = await self._ask_environment(HaltExecution, HaltExecutionResult)

        ret.success = result.succeeded
        if not ret.success:
            ret.msg = result.fail_message

        return ret

    @get_endpoint("/console_output")
    async def get_console_output(self, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output.

        Parameters
        ----------
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        ret = LatestConsoleResponse()

        log_centralizer = get_global_log_centralizer()
        return_lines = log_centralizer.lookup_queue(self._name, start=-lines)
        ret.lines = return_lines

        return ret

    @get_endpoint("/console_output/uid")
    async def get_console_output_uid(self) -> ConsoleUidResponse:
        """
        Get a unique identifier for the current state of the console output.

        This identifier has the property that anytime a new line is appended to
        the console, a new uid is generated.
        """
        log_centralizer = get_global_log_centralizer()
        uid = log_centralizer.get_uid_for_queue(self._name)

        return ConsoleUidResponse(uid=uid)

    def _on_exit(self):
        if self._environment_process_handle is not None and self._environment_process_handle.poll() is None:
            self._environment_process_handle.kill()
