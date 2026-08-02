import asyncio
from collections.abc import Callable
import time as ttime
from typing import cast

import httpx

from satellite.models import ManagerStatus, SuccessfulLoginResponse, UserInformation

from ._generated_base_client import BaseAsyncClient, BaseSyncClient


class OAuthAuthentication(httpx.Auth):
    """OAuth2 authentication provider for httpx clients."""

    requires_response_body = True

    def __init__(self, access_token: str, refresh_token: str, refresh_url: httpx.URL | str):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._refresh_url = refresh_url

    def _build_refresh_request(self) -> httpx.Request:
        return httpx.Request("POST", self._refresh_url, headers=self.make_headers_for_token(self.refresh_token))

    def make_headers_for_token(self, token: str, original_headers: httpx.Headers | None = None) -> httpx.Headers:
        """Return the authentication headers needed for 'token'."""
        headers = httpx.Headers({"Authorization": f"Bearer {token}"})
        if original_headers is not None:
            headers.update(original_headers)
        return headers

    def auth_flow(self, request: httpx.Request):
        """Override of the httpx.Auth method for manipulating the request."""
        request.headers = self.make_headers_for_token(self.access_token, request.headers)

        response = yield request
        if response.status_code == 401:
            refresh_response = yield self._build_refresh_request()
            refresh_response.raise_for_status()

            new_tokens = SuccessfulLoginResponse.model_validate(refresh_response.json())
            self.update_tokens(new_tokens.token, new_tokens.refresh_token)

            request.headers.pop("Authorization")
            request.headers = self.make_headers_for_token(self.access_token, request.headers)
            yield request

    def update_tokens(self, access_token: str, refresh_token: str):
        """Update the currently active tokens."""
        self.access_token = access_token
        self.refresh_token = refresh_token


class AsyncClient(BaseAsyncClient):
    """Python client for communication with the satellite server via asynchronous httpx requests."""

    async def get_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        """Perform a GET request and return the result."""
        request_url = endpoint

        if len(kwargs) > 0:
            request_url += "?"

            parameters = []
            for key, val in kwargs.items():
                parameters.append(f"{key}={str(val)}")

            request_url += "&".join(parameters)

        return await self.get(request_url)

    async def post_implementation(self, endpoint: str, **kwargs) -> httpx.Response:
        """Perform a POST request and return the result."""
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

    async def login(
        self, user_name: str, password: str, *, expiration_time: int | float | None = None
    ) -> SuccessfulLoginResponse:
        """
        Provide user credentials for acquiring permissions on the remote resources.

        This method already transparently configures this client with the tokens in case
        of success, so subsequent method calls will automatically add the relevant authentication
        information, and refresh the tokens when needed.

        Parameters
        ----------
        user_name : str
            Name of the user trying to log in.
        password : str
            Plaintext password for the given user.
        expiration_time : int or float, optional
            A custom expiration time for the returned access token. The server will cap this value
            to the default expiration time configured on there, so only lower values are allowed.

        Returns
        -------
        SuccessfulLoginResponse
            The tokens and login information of the request.
        """
        endpoint = "/login"
        if expiration_time is not None:
            endpoint += f"?override_expiration_time={expiration_time}"

        response = await self.post(endpoint, data={"username": user_name, "password": password})
        response.raise_for_status()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())

        auth_handler = OAuthAuthentication(
            parsed_response.token, parsed_response.refresh_token, self.base_url.join("/session_refresh")
        )

        self.auth = auth_handler

        return parsed_response

    async def logout(self):
        """Revoke permissions of the currently configured authentication tokens."""
        if self.auth is None:
            return

        current_auth = cast(OAuthAuthentication, self.auth)

        access_token_header = current_auth.make_headers_for_token(current_auth.access_token)
        refresh_token_header = current_auth.make_headers_for_token(current_auth.refresh_token)

        response = await self.post("/logout", headers=access_token_header)
        response.raise_for_status()

        response = await self.post("/logout", headers=refresh_token_header)
        response.raise_for_status()

    async def refresh_session(self, *, expiration_time: int | float | None = None) -> SuccessfulLoginResponse:
        """
        Attempt to refresh the authentication tokens without user information.

        Parameters
        ----------
        expiration_time : int or float, optional
            A custom expiration time for the returned access token. The server will cap this value
            to the default expiration time configured on there, so only lower values are allowed.

        Returns
        -------
        SuccessfulLoginResponse
            The new tokens and login information of the request.
        """
        if self.auth is None:
            raise RuntimeError("No authentication is configured on the client. Make sure to call 'login' first.")

        current_auth = cast(OAuthAuthentication, self.auth)

        refresh_token_header = current_auth.make_headers_for_token(current_auth.refresh_token)

        endpoint = "/session_refresh"
        if expiration_time is not None:
            endpoint += f"?override_expiration_time={expiration_time}"

        response = await self.post(endpoint, headers=refresh_token_header)
        response.raise_for_status()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        current_auth.update_tokens(parsed_response.token, parsed_response.refresh_token)

        return parsed_response

    async def whoami(self) -> UserInformation:
        """Query information about the owner of the currently configured authentication token."""
        response = await self.get("/whoami")
        response.raise_for_status()
        parsed_response = UserInformation.model_validate(response.json())
        return parsed_response


class SyncClient(BaseSyncClient):
    """Python client for communication with the satellite server via synchronous httpx requests."""

    def __init__(self, server_address: httpx.URL | str, queue_name: str | None = None, **kwargs):
        self._loop = asyncio.new_event_loop()
        self._client = AsyncClient(server_address, queue_name, **kwargs)

    def wait_for_condition(
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

        while not condition(self.status()):
            ttime.sleep(0.05)

            if timeout is not None and ttime.time() - _initial_time >= timeout:
                return False

        return True

    def wait_for_idle(self, *, timeout: int | None = 600) -> bool:
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

        return self.wait_for_condition(condition, timeout=timeout)

    def wait_for_idle_or_paused(self, *, timeout: int | None = 600) -> bool:
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

        return self.wait_for_condition(condition, timeout=timeout)

    def wait_for_idle_or_running(self, *, timeout: int | None = 600) -> bool:
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

        return self.wait_for_condition(condition, timeout=timeout)
