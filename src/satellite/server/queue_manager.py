import asyncio
import atexit
from collections.abc import Callable
from functools import partial, update_wrapper, wraps
import logging
import os
import subprocess
import time
from typing import Annotated, Any, Literal, no_type_check
from uuid import UUID, uuid4 as create_uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Security, status

from satellite.server.configuration import ManagerConfiguration
from satellite.server.ipc import IPCCommunicationPair, create_server_from_event_loop
from satellite.server.persistence import create_backend_for_configuration
from satellite.server.security import get_current_user
from satellite.server.security.main import get_current_user_group

from ..annotations import (
    DeviceAnnotation,
    PlanAnnotation,
    ValidationError,
    validate_plan,
)
from ..models import (
    AllowedDevicesResponse,
    AllowedPlansResponse,
    ConsoleUidResponse,
    ExecutionConfiguration,
    GenericResponse,
    HistoryResponse,
    LatestConsoleResponse,
    LockInformation,
    LockResponse,
    ManagerStatus,
    QueueAddRemoveBatchResponse,
    QueueAddRemoveResponse,
    QueueItem,
    QueueResponse,
    RunEngineRunsResponse,
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
    ListExecutingRuns,
    ListExecutingRunsResult,
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

            extra_dependencies = [Depends(self._validate_lock_key)]
            if "dependencies" in kwargs:
                kwargs["dependencies"].extend(extra_dependencies)
            else:
                kwargs["dependencies"] = extra_dependencies

            getattr(self._router, method)(*args, **kwargs)(endpoint)

        return self._router

    async def _check_for_enqueued_message(self, force_update_status: bool, *, bypass: bool = False) -> Any:
        """
        Retrieve a new message from the environment, if available.

        Parameters
        ----------
        force_update_status : bool
            If True, change the manager state uid and modification time even when
            the actual state remains the same.
        bypass : bool, optional
            If True, return the enqueued message without going through the default handlers.

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

        if bypass:
            return return_value

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

                # Trigger autostart if enabled
                if self._status.execution_configuration.autostart_enabled and self._status.items_in_queue != 0:
                    await self._enqueue_next_item_for_running()
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
            case ProvidedItemFinished(_uuid, item, stop_queue):
                self._status.running_item_uid = None

                await self._persistence_backend.history_insert_item(item, 0)

                self._status.items_in_history += 1
                self._status.plan_history_uid = create_uuid()

                if self._status.execution_configuration.loop_mode:
                    await self._persistence_backend.queue_insert_item(item)

                    self._status.items_in_queue += 1
                    self._status.plan_queue_uid = create_uuid()

                stop_execution = (
                    self._status.queue_stop_pending  # User has asked for a stop
                    or self._status.items_in_queue == 0  # No more items to run
                    or stop_queue  # A 'queue_stop' instruction has ran
                    or item.execute_method == "execute"  # Item was ran from a '/queue/execute' route
                    or (
                        not self._status.execution_configuration.ignore_errors and item.has_failed_execution()
                    )  # The item has failed
                )

                if stop_execution:
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
            case ListExecutingRunsResult(_uuid, _r, run_list_uid):
                self._status.run_list_uid = run_list_uid
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
                await self._environment_conn.close()
                self._environment_conn = None

        while (await self._check_for_enqueued_message(force_update_status)) is not None:
            pass

    async def _ask_environment(self, req_class: type, rsp_class: type, *args, bypass: bool = False, **kwargs) -> Any:
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
        bypass : bool, optional
            If True, bypass the default response handlers.
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
            return_value = await self._check_for_enqueued_message(False, bypass=bypass)

            if return_value is not None and isinstance(return_value, rsp_class) and return_value.uuid == message_uuid:
                response = return_value

                break

            await asyncio.sleep(0.025)

        return response

    async def _retrieve_and_cache_plan_annotations_from_environment(self) -> dict[str, PlanAnnotation] | None:
        # Get current annotations if environment exists
        await self.check_environment_process()
        if self._environment_process_handle is None:
            return

        response = await self._ask_environment(RetrieveAllPlanAnnotations, RetrieveAllPlanAnnotationsResult)
        if response is None:
            self._logger.warning("Failed to retrieve plan annotations response from environment.")
            return

        _ret = response.value

        # Cache the received value.
        await self._persistence_backend.set_existing_plans(_ret)
        self._status.plans_existing_uid = self._persistence_backend.existing_plans_uid
        self._status.plans_allowed_uid = self._persistence_backend.existing_plans_uid
        await self._status.update_manager_state(self._status.manager_state, force=True)

        return _ret

    async def _retrieve_and_cache_device_annotations_from_environment(self) -> dict[str, DeviceAnnotation] | None:
        # Get current annotations if environment exists
        await self.check_environment_process()
        if self._environment_process_handle is None:
            return

        response = await self._ask_environment(RetrieveAllDeviceAnnotations, RetrieveAllDeviceAnnotationsResult)
        if response is None:
            self._logger.warning("Failed to retrieve device annotations response from environment.")
            return

        _ret = response.value

        # Cache the received value.
        await self._persistence_backend.set_existing_devices(_ret)
        self._status.devices_existing_uid = self._persistence_backend.existing_devices_uid
        self._status.devices_allowed_uid = self._persistence_backend.existing_devices_uid
        await self._status.update_manager_state(self._status.manager_state, force=True)

        return _ret

    async def _retrieve_plan_annotations(self, *, allow_cached: bool = True) -> dict[str, PlanAnnotation] | None:
        if allow_cached:
            # Retrieve cached value, it there is one.
            annotation = await self._persistence_backend.get_existing_plans()
            if isinstance(annotation, dict) and len(annotation) != 0:
                return annotation

        # Get current annotations if environment exists
        annotations = await self._retrieve_and_cache_plan_annotations_from_environment()
        if annotations is None:
            self._logger.info("Failed to retrieve plan annotations.")
            return

        return annotations

    async def _retrieve_annotation_for_plan(self, item: QueueItem) -> PlanAnnotation | None:
        # Retrieve cached value, it there is one.
        annotation = await self._persistence_backend.get_existing_plans(sub_key=item.name)
        if isinstance(annotation, PlanAnnotation):
            return annotation

        # Get current annotations if environment exists
        annotations = await self._retrieve_and_cache_plan_annotations_from_environment()
        if annotations is None:
            self._logger.info("Failed to retrieve plan annotations for item '%s'.", item.name)
            return

        return annotations.get(item.name)

    async def _retrieve_device_annotations(self, *, allow_cached: bool = True) -> dict[str, DeviceAnnotation] | None:
        if allow_cached:
            # Retrieve cached value, it there is one.
            annotations = await self._persistence_backend.get_existing_devices()
            if isinstance(annotations, dict) and len(annotations) != 0:
                return annotations

        # Get current annotations if environment exists
        annotations = await self._retrieve_and_cache_device_annotations_from_environment()
        if annotations is None:
            self._logger.info("Failed to retrieve device annotations.")
            return

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
    async def ping(self) -> dict[Literal["message"], Literal["pong"]]:
        """Test connectivity with the queue manager. Always responds 'pong'."""
        return {"message": "pong"}

    @get_endpoint("/status", dependencies=[Security(get_current_user, scopes=["read:status"])])
    async def status(self) -> ManagerStatus:
        """Retrieve the current state of the manager."""
        await self.check_environment_process()

        return self._status

    async def _validate_lock_key(self, request: Request, lock_key: str | None = None):
        info = self._status.lock_info

        if info.is_locked_for_endpoint(request.url.path):
            if lock_key is not None and lock_key in {info.lock_key, info.emergency_lock_key}:
                return

            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"The manager is currently locked by user '{info.user}': {info.note}.",
            )

    @post_endpoint("/lock")
    async def add_lock_for_manager_operations(
        self,
        lock_key: str,
        lock_environment: Annotated[bool, Query(alias="environment")] = False,
        lock_queue: Annotated[bool, Query(alias="queue")] = False,
        note: str | None = None,
        user: Annotated[str, Security(get_current_user, scopes=["write:manager:lock"])] = "default",
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
        ret = LockResponse(lock_info_uid=self._status.lock_info_uid)

        if lock_environment and self._status.lock_info.is_environment_locked:
            ret.success = False
            ret.msg = "Failed to lock environment: It is already locked."

            return ret

        if lock_queue and self._status.lock_info.is_queue_locked:
            ret.success = False
            ret.msg = "Failed to lock queue: It is already locked."

            return ret

        if not lock_environment and not lock_queue:
            ret.success = False
            ret.msg = "Failed to lock environment / queue: At least one of those must be specified."

            return ret

        lock_information = LockInformation(environment=lock_environment, queue=lock_queue, user=user, note=note)
        lock_information.lock_key = lock_key
        if self._configuration.operation.emergency_key is not None:
            lock_information.emergency_lock_key = self._configuration.operation.emergency_key

        self._status.lock_info = lock_information
        self._status.lock_info_uid = create_uuid()

        await self._status.update_manager_state(state=self._status.manager_state, force=True)

        ret.lock_info = self._status.lock_info
        ret.lock_info_uid = self._status.lock_info_uid

        return ret

    @post_endpoint("/unlock", dependencies=[Security(get_current_user, scopes=["write:manager:lock"])])
    async def remove_lock_for_manager_operations(
        self,
        lock_key: str,
    ) -> LockResponse:
        """
        Unlock the manager, allowing other users to access write endpoints.

        Parameters
        ----------
        lock_key : str
            The lock key currently being used.
        """
        ret = LockResponse(lock_info_uid=self._status.lock_info_uid)

        if lock_key not in {self._status.lock_info.lock_key, self._status.lock_info.emergency_lock_key}:
            ret.success = False
            ret.msg = "Failed to unlock environment / queue: The provided lock key is not correct."

            return ret

        self._status.lock_info = LockInformation()  # Reset lock information
        self._status.lock_info_uid = create_uuid()

        await self._status.update_manager_state(state=self._status.manager_state, force=True)

        ret.lock_info = self._status.lock_info
        ret.lock_info_uid = self._status.lock_info_uid

        return ret

    @post_endpoint("/queue/mode/set")
    async def set_queue_mode(
        self,
        mode: Annotated[ExecutionConfiguration | Literal["default"] | None, Body(embed=True)] = None,
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
        ret = GenericResponse()

        if mode is not None:
            if mode == "default":
                self._status.execution_configuration = ExecutionConfiguration()
            else:
                self._status.execution_configuration = mode
        else:
            if loop is not None:
                self._status.execution_configuration.loop_mode = loop
            if ignore_failures is not None:
                self._status.execution_configuration.ignore_errors = ignore_failures
            if autostart is not None:
                self._status.execution_configuration.autostart_enabled = autostart

        await self._status.update_manager_state(self._status.manager_state, force=True)

        # Trigger autostart if enabled
        if self._status.execution_configuration.autostart_enabled and self._status.items_in_queue != 0:
            await self._enqueue_next_item_for_running()

        return ret

    @post_endpoint("/queue/autostart")
    async def set_queue_autostart(self, enable: bool, lock_key: str | None = None) -> GenericResponse:
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
        return await self.set_queue_mode(auto_start=enable, lock_key=lock_key)

    def _accept_environment_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Callback for a `asyncio.Server` instance when creating a new connection."""
        if self._environment_conn_server is None:
            raise RuntimeError

        self._environment_conn_server[0].stop()

        if self._environment_conn is not None:
            self._logger.error("Creating a new socket connection while there's already an existing one.")

            raise RuntimeError

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

    @post_endpoint("/environment/open", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
    async def environment_open(self, lock_key: str | None = None) -> GenericResponse:
        """
        Open a new environment for plan execution.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        ret = GenericResponse()

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

        # Reload annotation caches with the new environment.
        await self._retrieve_plan_annotations(allow_cached=False)
        await self._retrieve_device_annotations(allow_cached=False)

        return ret

    @post_endpoint("/environment/close", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
    async def environment_close(self, lock_key: str | None = None) -> GenericResponse:
        """
        Close the currently active environment.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.

        """
        ret = GenericResponse()

        await self.check_environment_process()

        if self._environment_process_handle is None:
            ret.success = False
            ret.msg = "Could not close the environment, since it is already closed."
            return ret

        await self._ask_environment(CloseEnvironment, CloseEnvironmentResult)
        await self.check_environment_process(force_update_status=True)

        return ret

    @post_endpoint("/environment/destroy", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
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

        if self._environment_process_handle is None:
            ret.success = False
            ret.msg = "Could not destroy the environment, since it is already closed."
            return ret

        self._environment_process_handle.terminate()

        await self.check_environment_process(force_update_status=True)

        return ret

    @get_endpoint("/history/get", dependencies=[Security(get_current_user, scopes=["read:history"])])
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

        # NOTE: If offset == 0, the user probably didn't change the default, so if the history
        # is currently empty, the more intuitive response is to reply with the empty history.
        if offset >= self._status.items_in_history and offset != 0:
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

    @post_endpoint("/history/clear", dependencies=[Security(get_current_user, scopes=["write:history:edit"])])
    async def history_clear(self) -> GenericResponse:
        """Clear the history of previously ran plans."""
        ret = GenericResponse()

        await self._persistence_backend.history_clear()
        self._status.items_in_history = 0

        return ret

    @get_endpoint("/queue/get", dependencies=[Security(get_current_user, scopes=["read:queue"])])
    async def queue_get(self) -> QueueResponse:
        """Retrieve a list of all items currently in the queue."""
        await self.check_environment_process()

        queue = await self._persistence_backend.queue_get()
        items_in_queue = [item for item in queue if item.uid is not None]
        return QueueResponse(items=items_in_queue, plan_queue_uid=self._status.plan_queue_uid)

    @post_endpoint("/queue/clear", dependencies=[Security(get_current_user, scopes=["write:queue:edit"])])
    async def queue_clear(self, lock_key: str | None = None) -> GenericResponse:
        """
        Remove all items currently in the queue.

        Parameters
        ----------
        lock_key : str, optional
            The lock key currently being used.
        """
        ret = GenericResponse()

        await self._persistence_backend.queue_clear()
        self._status.items_in_queue = 0

        return ret

    async def _validate_queue_item[T: GenericResponse](self, ret: T, item: QueueItem, *, user_group: str) -> T:
        if item.type == "plan":
            group_permissions = self._configuration.authorization.resource_access_authorization.group_permissions
            if user_group in group_permissions and not group_permissions[user_group].is_plan_allowed(item.name):
                ret.success = False
                ret.msg = (
                    "Failed to add item to queue:"
                    f"The current user (group: '{user_group}') doesn't have access to plan '{item.name}'."
                )
                return ret

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

        return ret

    async def _get_queue_position(
        self, position: int | Literal["front", "back"] | None = None, uid: UUID | str | None = None
    ) -> int | None:
        """Common method for retrieving the index of a queue item."""
        if uid is not None:
            index, _ = await self._persistence_backend.queue_get_item(uuid=uid)
            return index

        if isinstance(position, int):
            return position

        match position:
            case "front":
                return 0
            case "back":
                return None
            case None:
                pass

        raise RuntimeError("Failed to calculate queue position: Both 'position' and 'uid' are None.")

    async def _parse_ending_queue_position(
        self,
        pos: int | Literal["front", "back"] | None = None,
        before_uid: UUID | str | None = None,
        after_uid: UUID | str | None = None,
    ) -> tuple[GenericResponse, int | None]:
        """Common method for parsing destination position arguments into a queue position index."""
        ret = GenericResponse()

        set_position_parameters = [_p for _p in {"pos", "before_uid", "after_uid"} if locals()[_p] is not None]
        if len(set_position_parameters) != 1:
            ret.success = False
            ret.msg = (
                "Failed to add item to queue:"
                " Exactly one of 'pos', 'before_uid' or 'after_uid' must be set"
                f" (instead of {set_position_parameters})."
            )
            return ret, None

        try:
            uid = before_uid or after_uid
            index = await self._get_queue_position(position=pos, uid=uid)

            if after_uid is not None and isinstance(index, int):
                if index == self._status.items_in_queue - 1:
                    index = None
                else:
                    index += 1
        except Exception:
            ret.success = False
            ret.msg = f"Failed to add item to queue: No item with uid '{uid}' could be found."
            return ret, None

        if isinstance(index, int) and index >= self._status.items_in_queue:
            ret.success = False
            ret.msg = (
                f"Failed to add item to queue: Position '{index}' is outside the range allowed in the current queue."
            )
            return ret, None

        return ret, index

    @post_endpoint("/queue/item/add")
    async def queue_item_add(
        self,
        item: Annotated[QueueItem, Body(embed=True)],
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        user: Annotated[str, Security(get_current_user, scopes=["write:queue:edit"])] = "default",
        user_group: Annotated[str, Security(get_current_user_group)] = "primary",
        lock_key: str | None = None,
        *,
        ignore_validation: Annotated[bool, Query(include_in_schema=False)] = False,
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
        ret = QueueAddRemoveResponse()

        if not ignore_validation:
            ret = await self._validate_queue_item(ret, item, user_group=user_group)
            if not ret.success:
                return ret

        # Populate information
        if item.uid is None:  # No UID means this item is (probably) fresh new
            item.creation_user = user
            item.creation_user_group = user_group
        item.last_modification_user = user
        item.last_modification_user_group = user_group

        item.uid = create_uuid()

        parse_return, index = await self._parse_ending_queue_position(pos, before_uid, after_uid)
        if not parse_return.success:
            ret.success = parse_return.success
            ret.msg = parse_return.msg
            return ret

        await self._persistence_backend.queue_insert_item(item, index)

        # Prepare return
        self._status.plan_queue_uid = create_uuid()
        self._status.items_in_queue = await self._persistence_backend.queue_length()

        ret.queue_size = self._status.items_in_queue
        ret.item = item

        await self.check_environment_process(force_update_status=True)

        # Trigger autostart if enabled
        if self._status.worker_environment_exists and self._status.execution_configuration.autostart_enabled:
            await self._enqueue_next_item_for_running()

        return ret

    @post_endpoint("/queue/item/add/batch")
    async def queue_item_add_in_batch(
        self,
        items: Annotated[list[QueueItem], Body(embed=True)],
        pos: int | Literal["front", "back"] = "back",
        before_uid: str | None = None,
        after_uid: str | None = None,
        user: Annotated[str, Security(get_current_user, scopes=["write:queue:edit"])] = "default",
        user_group: Annotated[str, Security(get_current_user_group)] = "primary",
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
        ret = QueueAddRemoveBatchResponse()

        for item in items:
            item_return = GenericResponse()
            item_return = await self._validate_queue_item(item_return, item, user_group=user_group)

            if not item_return.success:
                ret.success = item_return.success
                ret.msg = item_return.msg

                return ret

        new_items = []
        for item in items:
            item_return = await self.queue_item_add(
                item=item,
                pos=pos,
                before_uid=before_uid,
                after_uid=after_uid,
                user=user,
                user_group=user_group,
                lock_key=lock_key,
                ignore_validation=True,
            )

            if not item_return.success:
                ret.success = item_return.success
                ret.msg = item_return.msg

                return ret

            new_items.append(item_return.item)

        ret.queue_size = self._status.items_in_queue
        ret.items = new_items

        return ret

    @post_endpoint("/queue/item/remove", dependencies=[Security(get_current_user, scopes=["write:queue:edit"])])
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

    @post_endpoint("/queue/item/remove/batch", dependencies=[Security(get_current_user, scopes=["write:queue:edit"])])
    async def queue_item_remove_in_batch(
        self,
        uids: Annotated[list[UUID], Body(embed=True)],
        ignore_missing: bool = True,
        lock_key: str | None = None,
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
        ret = QueueAddRemoveBatchResponse()
        ret.items = []

        for uid in uids:
            item_return = await self.queue_item_remove(uid=str(uid), lock_key=lock_key)

            if not item_return.success and not ignore_missing:
                ret.success = item_return.success
                ret.msg = item_return.msg

                return ret

            if item_return.success:
                ret.items.append(item_return.item)

        ret.queue_size = self._status.items_in_queue
        return ret

    @post_endpoint("/queue/item/update")
    async def queue_item_update(
        self,
        item: Annotated[QueueItem, Body(embed=True)],
        replace: bool = False,
        user: Annotated[str, Security(get_current_user, scopes=["write:queue:edit"])] = "default",
        user_group: Annotated[str, Security(get_current_user_group)] = "primary",
        lock_key: str | None = None,
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
        ret = QueueAddRemoveResponse()

        if item.uid is None:
            ret.success = False
            ret.msg = "Failed to update item: The provided item has no uid."
            return ret

        ret = await self._validate_queue_item(ret, item, user_group=user_group)
        if not ret.success:
            return ret

        try:
            position_in_queue = await self._get_queue_position(uid=item.uid)
        except RuntimeError:
            ret.success = False
            ret.msg = "Failed to update item: No item with such uid exists on the queue."
            return ret

        item.last_modification_user = user
        item.last_modification_user_group = user_group

        if replace:
            item.uid = create_uuid()

        await self._persistence_backend.queue_pop_item(position_in_queue)
        ret.queue_size = await self._persistence_backend.queue_insert_item(item, position_in_queue)

        ret.item = item

        return ret

    @post_endpoint("/queue/item/move", dependencies=[Security(get_current_user, scopes=["write:queue:edit"])])
    async def queue_item_move(
        self,
        original_position: Annotated[int | Literal["front", "back"] | None, Query(alias="pos")] = None,
        uid: UUID | None = None,
        destination_position: Annotated[int | Literal["front", "back"] | None, Query(alias="pos_dest")] = None,
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
        ret = QueueAddRemoveResponse()

        try:
            original_index = await self._get_queue_position(position=original_position, uid=uid)
        except RuntimeError as exc:
            ret.success = False
            ret.msg = str(exc)
            return ret

        parse_response, destination_index = await self._parse_ending_queue_position(
            pos=destination_position, before_uid=before_uid, after_uid=after_uid
        )
        if not parse_response.success:
            ret.success = parse_response.success
            ret.msg = parse_response.msg
            return ret

        item = await self._persistence_backend.queue_pop_item(original_index)
        if destination_index is not None and original_index is not None and original_index < destination_index:
            destination_index -= 1
        await self._persistence_backend.queue_insert_item(item, destination_index)

        ret.queue_size = self._status.items_in_queue
        ret.item = item

        return ret

    @post_endpoint("/queue/item/move/batch", dependencies=[Security(get_current_user, scopes=["write:queue:edit"])])
    async def queue_item_move_in_batch(
        self,
        uids: Annotated[list[UUID], Body(embed=True)],
        destination_position: Annotated[int | Literal["front", "back"] | None, Query(alias="pos_dest")] = None,
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
        ret = QueueAddRemoveBatchResponse()
        ret.items = []

        original_uids: list[tuple[UUID, int | None]] = []
        try:
            for uid in uids:
                original_uids.append((uid, await self._get_queue_position(uid=uid)))

            # NOTE: This seems weird at first glance, but before doing 'sorted', the list is in the
            # order that the uids came in. Here, we sort by the current queue positions if needed.
            if not reorder:
                original_uids = sorted(original_uids, key=lambda x: x[1])
        except RuntimeError as exc:
            ret.success = False
            ret.msg = str(exc)
            return ret

        parse_response, destination_position = await self._parse_ending_queue_position(
            pos=destination_position, before_uid=before_uid, after_uid=after_uid
        )
        if not parse_response.success:
            ret.success = parse_response.success
            ret.msg = parse_response.msg
            return ret

        for uid, _ in original_uids:
            # Avoid the call to 'queue_item_move' thinking we didn't pass any arguments
            if destination_position is None:
                destination_position = "back"

            item_response = await self.queue_item_move(
                uid=uid, destination_position=destination_position, lock_key=lock_key
            )
            if not item_response.success:
                ret.success = item_response.success
                ret.msg = item_response.msg
                return ret

            # Get next position - after the just-moved item
            parse_response, destination_position = await self._parse_ending_queue_position(after_uid=uid)
            if not parse_response.success:
                ret.success = parse_response.success
                ret.msg = parse_response.msg
                return ret

            ret.items.append(item_response.item)

        ret.queue_size = self._status.items_in_queue
        return ret

    @post_endpoint("/queue/item/execute")
    async def execute_item(
        self,
        item: Annotated[QueueItem, Body(embed=True)],
        user: Annotated[str, Security(get_current_user, scopes=["write:manager:control"])] = "default",
        user_group: Annotated[str, Security(get_current_user_group)] = "primary",
        lock_key: str | None = None,
    ) -> QueueAddRemoveResponse:
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
        ret = QueueAddRemoveResponse()

        await self.check_environment_process()

        if not self._status.worker_environment_exists:
            ret.success = False
            ret.msg = "Failed to execute item: No execution environment exists."
            return ret

        if self._status.manager_state != "idle":
            ret.success = False
            ret.msg = "Failed to execute item: Manager is not in the 'idle' state."
            return ret

        ret = await self._validate_queue_item(ret, item, user_group=user_group)
        if not ret.success:
            return ret

        item.execute_method = "execute"

        item.last_modification_user = user
        item.last_modification_user_group = user_group

        if item.uid is None:
            item.uid = create_uuid()

        env_return = await self._ask_environment(RunProvidedItem, RunProvidedItemResponse, item, bypass=True)
        if env_return is None:
            ret.success = False
            ret.msg = "Failed to execute item: Failed to submit item for execution."

        self._status.running_item_uid = item.uid

        ret.queue_size = self._status.items_in_queue
        ret.item = item

        return ret

    @post_endpoint("/queue/start", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
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

        await self.check_environment_process()

        new_ret = await self._enqueue_next_item_for_running()
        if new_ret.msg == "":
            new_ret.msg = ret.msg

        return new_ret

    @post_endpoint("/queue/stop", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
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

    @post_endpoint("/queue/stop/cancel", dependencies=[Security(get_current_user, scopes=["write:manager:control"])])
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

        await self.check_environment_process()

        if not self._status.queue_stop_pending:
            self._logger.warning("Cancelled a queue stop while it was not scheduled for stopping.")
        self._status.queue_stop_pending = False

        return ret

    @post_endpoint("/re/pause", dependencies=[Security(get_current_user, scopes=["write:plans:control"])])
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

    @post_endpoint("/re/resume", dependencies=[Security(get_current_user, scopes=["write:plans:control"])])
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

    @post_endpoint("/re/stop", dependencies=[Security(get_current_user, scopes=["write:plans:control"])])
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

    @post_endpoint("/re/abort", dependencies=[Security(get_current_user, scopes=["write:plans:control"])])
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

    @post_endpoint("/re/halt", dependencies=[Security(get_current_user, scopes=["write:plans:control"])])
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

    @get_endpoint("/re/runs", dependencies=[Security(get_current_user, scopes=["read:status"])])
    @post_endpoint("/re/runs", dependencies=[Security(get_current_user, scopes=["read:status"])], deprecated=True)
    async def run_engine_runs(
        self,
        option: Literal["active", "open", "closed"] = "active",
        option_from_body: Annotated[str, Body(alias="option", embed=True, deprecated=True)] = "",
    ) -> RunEngineRunsResponse:
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
        ret = RunEngineRunsResponse(run_list_uid=self._status.run_list_uid)

        await self.check_environment_process()

        if not self._status.worker_environment_exists:
            ret.uid = self._status.run_list_uid
            return ret

        if option_from_body != "":
            option = option_from_body  # ty: ignore

        include_open = option in {"active", "open"}
        include_closed = option in {"active", "closed"}
        result: ListExecutingRunsResult = await self._ask_environment(
            ListExecutingRuns, ListExecutingRunsResult, include_open, include_closed
        )

        ret.uid = self._status.run_list_uid
        if result is not None:
            ret.runs = [UUID(x) for x in result.run_list]

        return ret

    @get_endpoint("/console_output", dependencies=[Security(get_current_user, scopes=["read:console"])])
    async def get_console_output(self, lines: int = 200) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output.

        Parameters
        ----------
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        uid = (await self.get_console_output_uid()).uid
        ret = LatestConsoleResponse(last_msg_uid=uid)

        log_centralizer = get_global_log_centralizer()
        return_lines = log_centralizer.lookup_queue(self._name, start=-lines)
        ret.lines = return_lines

        return ret

    @get_endpoint("/console_output_update", dependencies=[Security(get_current_user, scopes=["read:console"])])
    async def get_console_output_from_uid(
        self,
        last_msg_uid: UUID | None = None,
        last_msg_uid_from_body: Annotated[str, Body(alias="last_msg_uid", embed=True, deprecated=True)] = "",
        lines: int = 200,
    ) -> LatestConsoleResponse:
        """
        Retrieve the most recent lines of logging / console output, generated after some point.

        Parameters
        ----------
        last_msg_uid : UUID or str
            The uid (as returned by `/console_output/uid`) from which to start collecting lines.
        lines : int, optional
            Maximum amount of lines to retrieve. Defaults to 200.
        """
        if last_msg_uid is None:
            if last_msg_uid_from_body is None or last_msg_uid_from_body == "":
                return await self.get_console_output(lines=lines)
            last_msg_uid = UUID(last_msg_uid_from_body)

        uid = (await self.get_console_output_uid()).uid
        ret = LatestConsoleResponse(last_msg_uid=uid)

        log_centralizer = get_global_log_centralizer()
        return_lines = log_centralizer.lookup_queue_by_uid(self._name, last_msg_uid, limit=lines)
        if return_lines is None:
            ret.success = False
            ret.msg = f"Failed to retrieve console output: UID '{str(last_msg_uid)}' is not valid."
            return ret

        ret.lines = return_lines
        return ret

    @get_endpoint("/console_output/uid", dependencies=[Security(get_current_user, scopes=["read:console"])])
    async def get_console_output_uid(self) -> ConsoleUidResponse:
        """
        Get a unique identifier for the current state of the console output.

        This identifier has the property that anytime a new line is appended to
        the console, a new uid is generated.
        """
        log_centralizer = get_global_log_centralizer()
        uid = log_centralizer.get_uid_for_queue(self._name)

        return ConsoleUidResponse(uid=uid)

    @get_endpoint("/plans/allowed")
    async def allowed_plans(
        self, user_group: Annotated[str, Security(get_current_user_group, scopes=["read:queue"])]
    ) -> AllowedPlansResponse:
        """Retrieve a list of allowed plans for the current user."""
        ret = AllowedPlansResponse(plans_allowed_uid=self._status.plans_allowed_uid)

        existing_plans = await self._persistence_backend.get_existing_plans()
        if isinstance(existing_plans, dict):
            existing_plan_annotations = existing_plans
        elif isinstance(existing_plans, PlanAnnotation):
            existing_plan_annotations = {existing_plans.plan_name: existing_plans}
        else:
            existing_plan_annotations = {}

        group_permissions = self._configuration.authorization.resource_access_authorization.group_permissions
        if user_group not in group_permissions:
            ret.items = existing_plan_annotations

            return ret

        ret.items = {
            _k: existing_plan_annotations[_k]
            for _k in filter(group_permissions[user_group].is_plan_allowed, existing_plan_annotations.keys())
        }
        return ret

    @get_endpoint("/devices/allowed")
    async def allowed_devices(
        self, user_group: Annotated[str, Security(get_current_user_group, scopes=["read:queue"])]
    ) -> AllowedDevicesResponse:
        """Retrieve a list of allowed devices for the current user."""
        ret = AllowedDevicesResponse(devices_allowed_uid=self._status.devices_allowed_uid)

        existing_devices = await self._persistence_backend.get_existing_devices()
        if isinstance(existing_devices, dict):
            existing_device_names = existing_devices
        elif isinstance(existing_devices, DeviceAnnotation):
            existing_device_names = {existing_devices.device_name: existing_devices}
        else:
            existing_device_names = {}

        group_permissions = self._configuration.authorization.resource_access_authorization.group_permissions
        if user_group not in group_permissions:
            ret.items = existing_device_names

            return ret

        # TODO: ret.items = list(filter(group_permissions[user_group].is_device_allowed, existing_device_names))
        ret.items = existing_device_names
        return ret

    def _on_exit(self):
        if self._environment_process_handle is not None and self._environment_process_handle.poll() is None:
            self._environment_process_handle.kill()
