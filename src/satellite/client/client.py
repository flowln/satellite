import asyncio
from collections.abc import Callable
import time as ttime

import httpx

from satellite.models import ManagerStatus

from ._generated_base_client import BaseAsyncClient


class AsyncClient(BaseAsyncClient):
    """Python client for communication with the satellite server via asynchronous httpx requests."""

    async def _get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        request_url = endpoint

        if len(kwargs) > 0:
            request_url += "?"

            parameters = []
            for key, val in kwargs.items():
                parameters.append(f"{key}={str(val)}")

            request_url += "&".join(parameters)

        return await self.get(request_url)

    async def _post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self.post(endpoint, json=kwargs)

    async def wait_for_condition(
        self,
        condition: Callable[
            [
                ManagerStatus,
            ],
            bool,
        ],
        *,
        timeout: int | None = 600,
    ) -> bool:
        """
        Wait for some state of the manager to be achieved before returning.

        Parameters
        ----------
        condition : callable
            A function that returns True when the desired state is achieved.
        timeout : int, optional
            The maximum amount of time to wait before returning, in seconds.
            A value of None specifies no timeout. Defaults to 10 minutes (600 seconds).

        Returns
        -------
        bool
            Whether this method returned because the condition was achieved (True), or
            because the timeout expired (False).
        """
        _initial_time = ttime.time()

        while not condition(await self.status()):
            await asyncio.sleep(0.05)

            if timeout is not None and ttime.time() - _initial_time >= timeout:
                return False

        return True

    async def wait_for_idle(self, *, timeout: int | None = 600) -> bool:
        """
        Wait for the queue manager to be in the 'idle' state.

        Parameters
        ----------
        timeout : int, optional
            The maximum amount of time to wait before returning, in seconds.
            A value of None specifies no timeout. Defaults to 10 minutes (600 seconds).

        Returns
        -------
        bool
            Whether this method returned because the condition was achieved (True), or
            because the timeout expired (False).
        """

        def condition(status: ManagerStatus) -> bool:
            return status.manager_state == "idle"

        return await self.wait_for_condition(condition, timeout=timeout)

    async def wait_for_idle_or_paused(self, *, timeout: int | None = 600) -> bool:
        """
        Wait for the queue manager to be in either the 'idle' or the 'paused' state.

        Parameters
        ----------
        timeout : int, optional
            The maximum amount of time to wait before returning, in seconds.
            A value of None specifies no timeout. Defaults to 10 minutes (600 seconds).

        Returns
        -------
        bool
            Whether this method returned because the condition was achieved (True), or
            because the timeout expired (False).
        """

        def condition(status: ManagerStatus) -> bool:
            return status.manager_state in {"idle", "paused"}

        return await self.wait_for_condition(condition, timeout=timeout)

    async def wait_for_idle_or_running(self, *, timeout: int | None = 600) -> bool:
        """
        Wait for the queue manager to be in either the 'idle' or the 'executing_queue' state.

        Parameters
        ----------
        timeout : int, optional
            The maximum amount of time to wait before returning, in seconds.
            A value of None specifies no timeout. Defaults to 10 minutes (600 seconds).

        Returns
        -------
        bool
            Whether this method returned because the condition was achieved (True), or
            because the timeout expired (False).
        """

        def condition(status: ManagerStatus) -> bool:
            return status.manager_state in {"idle", "executing_queue"}

        return await self.wait_for_condition(condition, timeout=timeout)
