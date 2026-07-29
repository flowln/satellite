# ruff: noqa

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
    def __init__(self, server_address: httpx.URL | str, **kwargs):
        super().__init__(base_url=server_address, **kwargs)

    async def _get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError

    async def _post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        raise NotImplementedError
