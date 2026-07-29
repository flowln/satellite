import httpx

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
