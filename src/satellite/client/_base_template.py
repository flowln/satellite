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
