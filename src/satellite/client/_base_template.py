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

from typing import Literal
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

    async def _get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def _post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError
