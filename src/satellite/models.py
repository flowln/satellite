from collections.abc import Sequence
import datetime as dt
from importlib.metadata import version
from typing import Any, Literal, cast
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, computed_field

from satellite.annotations import DeviceAnnotation, PlanAnnotation


def create_timed_uuid(base_uuid: UUID, time: dt.datetime) -> UUID:
    """Create a UUID referent to the given datetime object."""
    return uuid5(base_uuid, time.isoformat())


def create_uuid() -> UUID:
    """Generate a new UUID."""
    return uuid4()


class SuccessfulLoginResponse(BaseModel):
    """Response of a successful 'login' request. Based on RFC 6749 - https://datatracker.ietf.org/doc/html/rfc6749#section-5.1"""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    token: str = Field(alias="access_token")
    """JWT Token asserting the user's permission to access resources."""

    refresh_token: str
    """JWT Token enabling the user to generate new tokens for itself."""

    expires_in: int | float
    """
    Time, in seconds, that this token will remain valid after generating it.

    It is redundant with the 'exp' claim of the token, but allows for JWT-unaware clients to use the API.
    """

    token_type: Literal["bearer"] = Field(default="bearer")
    """Specifies the way of using the returned token in subsequent API calls."""


class UserInformation(BaseModel):
    """Information about a user of the system, as returned by 'whoami'."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    user_name: str
    """Name of the current user."""

    user_group: str
    """Group the user currently belongs in."""

    scopes: Sequence[str] = Field(default=[])
    """API privileges this user has access to."""


type ManagerState = Literal["idle", "creating_environment", "closing_environment", "executing_queue", "paused"]


class ExecutionConfiguration(BaseModel):
    """Configuration options for executing items from the queue."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    loop_mode: bool = Field(alias="loop", default=False)
    """Add executed items to the back of the queue automatically."""
    ignore_errors: bool = Field(alias="ignore_failures", default=False)
    """Ignore the exit status of an item when checking whether to proceed execution to the next item in the queue."""
    autostart_enabled: bool = Field(alias="autostart", default=False)
    """Start execution whenever possible (i.e. the queue is not empty and the environment is ready to execute it)."""


class LockInformation(BaseModel):
    """Information on the state of a locked manager."""

    lock_key: str | None = Field(default=None, exclude=True)
    """The lock key associated with this information, if any."""
    emergency_lock_key: str = Field(default="emergency!", exclude=True)
    """The emergency lock key, used as a master key in case the original lock key is lost."""

    is_environment_locked: bool = Field(alias="environment", default=False)
    """Whether environment management endpoints are locked."""
    is_queue_locked: bool = Field(alias="queue", default=False)
    """Whether queue operation endpoints are locked."""

    user: str = Field(default="unauthenticated_public")
    """The user who requested the lock current lock."""

    note: str | None = Field(default=None)
    """A note the user who locked the manager left for other users."""

    lock_timestamp: dt.datetime = Field(alias="time", default_factory=lambda _: dt.datetime.now())
    """The timestamp when the lock was created."""

    def is_locked_for_endpoint(self, endpoint: str) -> bool:
        """Return whether the provided endpoint path is locked by the current locking state."""
        is_locked: bool = False
        if self.is_environment_locked:
            is_locked |= endpoint.endswith(
                (
                    "/environment/open",
                    "/environment/close",
                    "/environment/destroy",
                    "/queue/start",
                    "/queue/stop",
                    "/queue/stop/cancel",
                    "/queue/item/execute",
                    "/re/pause",
                    "/re/resume",
                    "/re/stop",
                    "/re/abort",
                    "/re/halt",
                )
            )
        if self.is_queue_locked:
            is_locked |= endpoint.endswith(
                (
                    "/queue/mode/set",
                    "/queue/autostart",
                    "/queue/item/add",
                    "/queue/item/add/batch",
                    "/queue/item/update",
                    "/queue/item/remove",
                    "/queue/item/remove/batch",
                    "/queue/item/move",
                    "/queue/item/move/batch",
                    "/queue/clear",
                    "/history/clear",
                )
            )
        return is_locked


class ManagerStatus(BaseModel):
    """Return result of a call to the '/status' endpoint."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    manager_uuid: UUID | None = Field(exclude=True, frozen=True, default=None)

    version: str = Field(
        alias="msg",
        default_factory=lambda _: f"Satellite v{version('satellite')}",
        frozen=True,
    )
    """Application name and version."""

    last_update_time: dt.datetime = Field(alias="time", default_factory=lambda _: dt.datetime.now())
    """Time of the last update to this manager, in ISO 8601 format."""

    items_in_queue: int = 0
    """Amount of items currently waiting in the queue."""
    items_in_history: int = 0
    """Amount of items kept in the history list."""

    running_item_uid: UUID | None = None
    plan_queue_uid: UUID = create_uuid()
    plan_history_uid: UUID = create_uuid()
    task_results_uid: UUID = create_uuid()
    plans_allowed_uid: UUID = create_uuid()
    devices_allowed_uid: UUID = create_uuid()
    plans_existing_uid: UUID = create_uuid()
    devices_existing_uid: UUID = create_uuid()
    run_list_uid: UUID = create_uuid()

    manager_state: ManagerState = "idle"
    """Current processing activity of the manager."""
    re_state: str | None = None

    worker_environment_state: Literal["initializing", "idle", "pausing", "paused", "closing", "closed", "running"] = (
        "closed"
    )
    worker_background_tasks: int = 0

    execution_configuration: ExecutionConfiguration = Field(alias="plan_queue_mode", default=ExecutionConfiguration())
    """Configuration options for executing items from the queue."""

    queue_stop_pending: bool = False
    pause_pending: bool = False

    worker_environment_exists: bool = False

    ip_kernel_state: str | None = None
    ip_kernel_captured: bool | None = None

    lock_info_uid: UUID = create_uuid()
    lock_info: LockInformation = Field(default=LockInformation())

    @computed_field
    @property
    def queue_autostart_enabled(self) -> bool:
        """Start execution whenever possible (i.e. the queue is not empty and the environment is ready to execute)."""
        # NOTE: This field only exists for bluesky-queueserver compatibility.

        # The following is the warning that would be generated if Pydantic didn't raise it
        # every time the status is serialized (annoying).

        # warnings.warn(
        #     "This field is deprecated. Use the respective field in the 'execution_configuration' field instead.",
        #     stacklevel=2,
        # )

        return self.execution_configuration.autostart_enabled

    @computed_field
    @property
    def status_uid(self) -> UUID:
        """Unique ID representing the current manager state. Any state change will mutate this field."""
        return create_timed_uuid(cast(UUID, self.manager_uuid), self.last_update_time)

    async def update_manager_state(self, state: ManagerState, *, force: bool = False):
        """Update the manager's 'state' attribute, together with the update time."""
        if state == self.manager_state and not force:
            return

        self.manager_state = state
        self.last_update_time = dt.datetime.now()


class QueueItem(BaseModel):
    """A representation of some future code execution, be it a plan or an instruction."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    uid: UUID | None = Field(alias="item_uid", default=None)
    """Unique ID of this item in the queue. Is 'None' if it was generated by a client instead of the queue server."""

    type: Literal["plan", "instruction"] = Field(alias="item_type", default="plan")
    """Type of operation this item represents."""

    execute_method: Literal["queue", "execute"] = Field(default="queue")
    """Whether this item was directly executed, or ran as a normal queue submission."""

    name: str
    """
    Human-readable name of this item.

    For the 'plan' item type, this represents the plan name to execute.

    For the 'instruction' item type, this must be one of the following:

    - 'queue_stop': Stop automatic execution of the queue at this point.
    """

    creation_user: str = Field(default="<no user>")
    """User that created this item originally."""
    creation_user_group: str | None = Field(default="<no user group>")
    """Group of the user that created this item originally, if the user belongs to one."""

    last_modification_user: str = Field(alias="user", default="<no user>")
    """Name of the user who last created / modified this item."""
    last_modification_user_group: str | None = Field(alias="user_group", default="<no user group>")
    """Group of the user who last created / modified this item, if the user belongs to one."""

    args: list[Any] = []
    """Positional arguments to the operation this item represents."""
    kwargs: dict[str, Any] = {}
    """Keyword arguments to the operation this item represents."""

    metadata: dict[str, Any] = Field(alias="meta", default={})
    """Optional metadata associated with this item."""

    @classmethod
    def from_str_uid(cls, uid: str):
        """Construct a mock QueueItem from the provided uid."""
        return QueueItem(item_uid=UUID(uid), name="")

    def __eq__(self, other):
        return self.uid == other.uid


class HistoryItem(QueueItem):
    """Description of an item that has already been processed."""

    exit_status: Literal["completed", "failed", "stopped", "aborted", "halted", "unknown"] = "unknown"

    run_uids: Sequence[str] = []
    scan_ids: Sequence[int] = []

    time_start: float = 0.0
    time_stop: float = 0.0

    msg: str = ""
    traceback: str = ""

    @classmethod
    def from_queue_item(cls, queue_item: QueueItem):
        """Create a matching HistoryItem from a previous QueueItem."""
        return HistoryItem(**queue_item.model_dump(by_alias=True))

    def has_failed_execution(self) -> bool:
        """Return whether the execution represents some kind of error (True), or success (False)."""
        return self.exit_status in {"failed", "aborted", "halted"}


class GenericResponse(BaseModel):
    """Generic response model for HTTP requests."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    success: bool = True
    """Indicates if the request was processed successfully."""

    msg: str = ""
    """Error message in case of failure. If 'success' = True, this doesn't mean anything."""


class LockResponse(GenericResponse):
    """Data returned by the '/lock' API endpoint."""

    lock_info_uid: UUID

    lock_info: LockInformation = Field(default=LockInformation())


class QueueResponse(GenericResponse):
    """Data returned by the 'queue_get' API endpoint."""

    items: list[QueueItem]
    """List of items currently in the queue, ordered from first to last."""

    running_item: dict = {}
    """Parameters of the currently running item. Empty if no item is currently running."""

    plan_queue_uid: UUID
    """Unique ID for the current state of the queue."""


class HistoryResponse(GenericResponse):
    """Data returned by the 'history_get' API endpoint."""

    items: Sequence[HistoryItem]
    """List of items currently in the history, ordered from most recent to oldest."""

    plan_history_uid: UUID
    """Unique ID for the current state of the history."""


class QueueAddRemoveResponse(GenericResponse):
    """Data returned by the 'queue_item_add' and 'queue_item_remove' operations."""

    queue_size: int | None = Field(alias="qsize", default=None)
    """Total number of items in the queue after the operation. If 'success' = False, None is returned instead."""

    item: QueueItem | None = None
    """The inserted / removed item, with the 'uid' attribute filled in. If 'success' = False, None is returned."""


class QueueAddRemoveBatchResponse(GenericResponse):
    """Data returned by the '/queue/item/add/batch' and '/queue/item/remove/batch' operations."""

    queue_size: int | None = Field(alias="qsize", default=None)
    """Total number of items in the queue after the operation. If 'success' = False, None is returned instead."""

    items: Sequence[QueueItem] | None = None
    """The inserted / removed items, with the 'uid' attribute filled in. If 'success' = False, None is returned."""


class RunEngineRunsResponse(GenericResponse):
    """Data returned by the 're/runs' API endpoint."""

    uid: UUID = Field(alias="run_list_uid")
    """Unique identifier for the current run list state."""

    runs: Sequence[UUID] = Field(alias="run_list", default=[])
    """List of run UUIDs matching the specified requirements."""


class LatestConsoleResponse(GenericResponse):
    """Data returned by the 'console_output' API endpoint."""

    uid: UUID = Field(alias="last_msg_uid")
    """Unique identifier for the current state of the console."""

    lines: Sequence[str] = Field(alias="console_output_msgs", default=[])
    """Text lines of past console output."""


class ConsoleUidResponse(GenericResponse):
    """Data returned by the 'console_output/uid' API endpoint."""

    uid: UUID
    """Unique identifier for the current state of the console."""


class AllowedPlansResponse(GenericResponse):
    """Data returned by the '/plans/allowed' API endpoint."""

    uid: UUID = Field(alias="plans_allowed_uid")
    """Unique identifier for the current list of allowed plans."""

    items: dict[str, PlanAnnotation] = Field(alias="plans_allowed", default={})
    """Dictionary of (plan name -> plan annotation) allowed for the current user group."""


class AllowedDevicesResponse(GenericResponse):
    """Data returned by the '/devices/allowed' API endpoint."""

    uid: UUID = Field(alias="devices_allowed_uid")
    """Unique identifier for the current list of allowed devices."""

    items: dict[str, DeviceAnnotation] = Field(alias="devices_allowed", default={})
    """Dictionary of (device name -> device annotation) allowed for the current user group."""
