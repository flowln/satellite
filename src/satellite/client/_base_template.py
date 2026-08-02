# ruff: noqa

# This file is a base implementation of the generated code at _generated_base_client.py.
# The flow to create a client is essentially as follows:
#
# 1. This file is parsed as an AST (Abstract Syntax Tree).
#
# 2. The source server file that contains the server-side implementations of the API endpoints is parsed as an AST.
#
# 3. The server AST is manipulated to extract the endpoint functions.
#
# 4. These functions are transformed and appended to this file's AST, to generate the base implementation.
#
# 5. The resulting AST is converted back into source code, and saved to a file.
#
# 6. This file is finally formatted and linted.
#
# These comments are not included in the output too, since they are not parsed into the AST.
# But the docstring below is, which is intended.
"""
This file is auto-generated! Do NOT manually edit it.

Instead, check 'main.py' for the logic that generates it or,
if applicable, change the final client code in 'client.py' instead.

This file was generated at:
Date: $generation_date
Git revision: $generation_git_revision
"""

from abc import abstractmethod
from collections.abc import Coroutine
import asyncio

from typing import Literal, Any
from uuid import UUID

import httpx

from satellite.models import (
    ConsoleUidResponse,
    GenericResponse,
    HistoryResponse,
    LatestConsoleResponse,
    ManagerStatus,
    QueueAddRemoveResponse,
    QueueItem,
    QueueResponse,
    SuccessfulLoginResponse,
    UserInformation,
)


class BaseAsyncClient(httpx.AsyncClient):
    def __init__(self, server_address: httpx.URL | str, queue_name: str | None = None, **kwargs):
        self._base_url = server_address
        self._queue_name = queue_name

        super().__init__(base_url=self.base_address, **kwargs)

    @property
    def queue_name(self) -> str | None:
        return self._queue_name

    @property
    def base_address(self) -> httpx.URL:
        if self.queue_name is None:
            return httpx.URL(self._base_url)
        return httpx.URL(self._base_url.join("/" + self.queue_name))

    async def get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    @abstractmethod
    async def login(
        self, user_name: str, password: str, *, expiration_time: int | float | None = None
    ) -> SuccessfulLoginResponse: ...

    @abstractmethod
    async def logout(self): ...

    @abstractmethod
    async def refresh_session(self, *, expiration_time: int | float | None = None) -> SuccessfulLoginResponse: ...

    @abstractmethod
    async def whoami(self) -> UserInformation: ...


class BaseSyncClient:
    def __init__(self, server_address: httpx.URL | str, queue_name: str | None = None, **kwargs):
        self._loop = asyncio.new_event_loop()
        self._client = BaseAsyncClient(server_address, queue_name, **kwargs)

        # NOTE: This method needs to be completely overriden by a subclass, since `_client` needs
        # to be of a subclass type of BaseAsyncClient that actually implements the needed methods.
        raise NotImplementedError

    @property
    def queue_name(self) -> str | None:
        return self._client.queue_name

    @property
    def base_address(self) -> httpx.URL:
        return self._client.base_address

    def _run_coroutine(self, coro: Coroutine) -> Any:
        result = None

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running in the current thread: we can run our own.

            task = self._loop.create_task(coro)
            result = self._loop.run_until_complete(task)
        else:
            # Loop already running in the current thread: run our own in a separate thread.

            def _execute_coro():
                nonlocal result

                result = self._loop.run_until_complete(coro)

            import threading

            worker = threading.Thread(target=_execute_coro, daemon=True)
            worker.start()
            worker.join(timeout=10.0)

        return result

    def get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        return self._run_coroutine(self._client.get_implementation(endpoint, **kwargs))

    def post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        return self._run_coroutine(self._client.post_implementation(endpoint, **kwargs))
