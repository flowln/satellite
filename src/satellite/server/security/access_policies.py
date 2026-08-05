from abc import abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from json import JSONDecodeError
import logging
import threading
import time as ttime
from typing import Literal, NamedTuple, TypedDict

import httpx

from . import ANONYMOUS_USER_NAME, API_SCOPES

logger = logging.getLogger("satellite.server.security.access_policy")


ScopeOperation = Literal["scopes_set", "scopes_add", "scopes_remove"]
type BasicAPIRolesType = dict[str, dict[ScopeOperation, Sequence[str]]]


class APIAccessPolicy:
    """Base class for API access policies."""

    @abstractmethod
    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return the API access scopes the specified user has access to."""
        raise NotImplementedError


class BasicAPIAccessPolicy(APIAccessPolicy):
    """Basic policy using a simple pre-populated user -> scopes dictionary."""

    def __init__(self, roles: BasicAPIRolesType | None = None):
        self._roles: dict[str, set[str]] = defaultdict(set)

        if roles is None:
            self._roles[ANONYMOUS_USER_NAME] = set(API_SCOPES.keys())

            return

        for user_name, operations in roles.items():
            for operation, scopes in operations.items():
                match operation:
                    case "scopes_set":
                        self._roles[user_name] = set(scopes)
                        break
                    case "scopes_add":
                        self._roles[user_name].update(scopes)
                    case "scopes_remove":
                        self._roles[user_name].difference_update(scopes)

    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return the API access scopes the specified user has access to."""
        return self._roles.get(user_name, set())

    @classmethod
    def get_roles_for_admin_power(cls, *users: *tuple[str]) -> BasicAPIRolesType:
        """Return an argument to BasicAPIAccessPolicy in which all provided users have all permissions."""
        return {user: {"scopes_set": list(API_SCOPES.keys())} for user in users}


class DictionaryUserType(TypedDict):
    """Configured information for a user in the dictionary API access policy."""

    roles: str | Sequence[str]

    displayed_name: str
    email: str


class DictionaryUserInformation(NamedTuple):
    """Non-authorization information about a user in the dictionary API access policy."""

    display_name: str
    email: str


class DictionaryAPIAccessPolicy(BasicAPIAccessPolicy):
    """Access policy using a dictionary of permissions, with default roles for easier configuration."""

    DEFAULT_OBSERVER_SCOPES = {"read:status", "read:queue", "read:history", "read:console"}
    DEFAULT_USER_SCOPES = {
        *DEFAULT_OBSERVER_SCOPES,
        "write:manager:control",
        "write:plan:control",
        "write:queue:edit",
        "write:history:edit",
    }
    DEFAULT_ADVANCED_SCOPES = {*DEFAULT_USER_SCOPES}
    DEFAULT_EXPERT_SCOPES = {*DEFAULT_USER_SCOPES}
    DEFAULT_ADMIN_SCOPES = {*DEFAULT_USER_SCOPES}

    def __init__(self, users: dict[str, DictionaryUserType] | None = None, roles: BasicAPIRolesType | None = None):
        super().__init__(roles=roles)

        self._roles.setdefault("observer", self.DEFAULT_OBSERVER_SCOPES)
        self._roles.setdefault("user", self.DEFAULT_USER_SCOPES)
        self._roles.setdefault("advanced", self.DEFAULT_ADVANCED_SCOPES)
        self._roles.setdefault("expert", self.DEFAULT_EXPERT_SCOPES)
        self._roles.setdefault("admin", self.DEFAULT_ADMIN_SCOPES)

        self._user_scopes: dict[str, set[str]] = defaultdict(set)
        self._user_information: dict[str, DictionaryUserInformation] = {}

        if users is not None:
            for user_name, user_information in users.items():
                self._configure_scopes_for_user(user_name, user_information)

    def _configure_scopes_for_user(self, user_name: str, user_information: DictionaryUserType):
        user_roles = user_information.get("roles", set())

        for role in user_roles:
            scopes = self._roles.get(role, set())

            self._user_scopes[user_name].update(scopes)
        self._user_information[user_name] = DictionaryUserInformation(
            user_information.get("displayed_name", ""), user_information.get("email", "")
        )

    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return the API access scopes the specified user has access to."""
        return self._user_scopes.get(user_name, set())


type ServerBasedExpectedResponse = dict[str, dict[str, dict[str, str]]]


class ServerBasedAPIAccessPolicy(DictionaryAPIAccessPolicy):
    """
    Access policy that delegates to an external server the responsability of knowing each user's permissions.

    This access policy works by sending an HTTP GET request to a remove server, and getting back a response in
    a previously agreed upon scheme, which is used for authorizing users.

    This scheme is roughly as follows:

    .. code:
        {
            <role name>: {
                <user name> : {<extra user informations ...>},
                <user name> : {<extra user informations ...>},
                ...
            },
            <role name> : {
                <user name> : {<extra user informations ...>},
                <user name> : {<extra user informations ...>},
                ...
            },
            ...
        }

    Parameters
    ----------
    roles : BasicAPIRolesType, optional
        Additional role definitions to add. By default, the defaults from the
        DictionaryAPIAccessPolicy will be used.
    mock_responses : dict, optional
        A dictionary of URL paths to server responses. When defined, the httpx client is
        mocked instead of doing a real request. Useful for testing.
    server : str, optional
        The server address to communicate with. Either this or 'base_url' must be set.
    base_url : str, optional
        The server address to communicate with. Either this or 'server' must be set.
    port : int, optional
        The port on which the remote server is listening to. Defaults to 8000.
    query_path : str, optional
        Specify a custom path for fetching the authorization data from the server. Extra variables
        can be specified with {variable}. Defaults to "/instrument/{instrument}/qserver/access".
    update_period : int, optional
        Time period (in seconds) between requesting new data from the server. Defaults to 600s.
    expiration_period : int, optional
        Amount of time since last being able to reach the server, after which all permissions are
        revoked until the server goes back up. Defaults to 2400s.
    """

    def __init__(
        self,
        roles: BasicAPIRolesType | None = None,
        mock_responses: dict[str, ServerBasedExpectedResponse] | None = None,
        *,
        server: str | None = None,
        base_url: str | None = None,
        port: int = 8000,
        query_path: str = "/instrument/{instrument}/qserver/access",
        update_period: int = 600,
        expiration_period: int = 2400,
        **kwargs,
    ):
        super().__init__(roles=roles)

        if server is None and base_url is None:
            logger.error(
                "Failed to configure server based API access policy: Either 'server' or 'base_url' must be provided."
            )

        query_path_populated = query_path
        try:
            query_path_populated = query_path.format_map(kwargs)
        except KeyError as exc:
            logger.error(f"Failed to populate query path for server-based authorization: Missing key {exc}.")

        base_url = base_url or server or "localhost"
        self._query_url = f"{base_url}:{port}{query_path_populated}"

        if mock_responses is not None:

            def _request_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=mock_responses.get(request.url.path))

            transport = httpx.MockTransport(_request_handler)
            self._client = httpx.Client(transport=transport)
        else:
            self._client = httpx.Client()

        self._last_update_time = 0

        self._update_time = update_period
        self._expire_time = expiration_period

        self._request_roles_lock = threading.Lock()
        self._thread_should_continue = True

        self._update_thread = threading.Thread(
            target=self._handle_periodic_server_communication, name="Authorization - Server communication", daemon=True
        )
        self._update_thread.start()

    def __del__(self):
        self._thread_should_continue = False

    def request_roles_from_server(self) -> bool:
        """Send a request to the server for retrieving an updated authorization document."""
        with self._request_roles_lock:
            logger.debug("Sending request for new data to '%s'.", self._query_url)

            response = self._client.get(self._query_url)
            if response.status_code != 200:
                logger.warning(
                    "Failed with status '%s' while trying to query '%s'.", response.reason_phrase, self._query_url
                )

                return False

            try:
                auth_document: ServerBasedExpectedResponse = response.json()
            except JSONDecodeError as exc:
                logger.error("Failed to parse received response from the authorization server.", exc_info=exc)

                return False

            self._last_update_time = ttime.time()

            logger.debug("Successfully requested data from the authorization server.")

            users_dict = defaultdict(dict)
            for role_name, users in auth_document.items():
                for user_name, user_informations in users.items():
                    if "roles" not in users_dict[user_name]:
                        users_dict[user_name]["roles"] = set()

                    users_dict[user_name]["roles"].add(role_name)
                    users_dict[user_name].update(user_informations)

            # Clear old permissions so there's no chance a scope isn't cleared when it should.
            self._user_scopes.clear()
            self._user_information.clear()

            for user_name, user_information in users_dict.items():
                self._configure_scopes_for_user(user_name, user_information)

            return True

    def _handle_periodic_server_communication(self):
        while self._thread_should_continue:
            response = self.request_roles_from_server()

            if not response and ttime.time() - self._last_update_time >= self._expire_time:
                logger.warning(
                    "We failed to communicate with the authorization server for more than '%s' seconds."
                    "Expiring all user permissions until a new successful connection can be made."
                )

                self._user_scopes.clear()
                self._user_information.clear()

            ttime.sleep(self._update_time)

    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return the API access scopes the specified user has access to."""
        if user_name not in self._user_scopes and ttime.time() - self._last_update_time > self._update_time:
            self.request_roles_from_server()

        return self._user_scopes.get(user_name, set())


class ResourceAccessPolicy:
    """Base class for resource access policies."""

    @abstractmethod
    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return whether the given user can access the specified resource."""
        raise NotImplementedError

    @abstractmethod
    def get_group_of_user(self, user_name: str) -> str:
        """Return the group this user is a part of."""
        raise NotImplementedError


class BasicResourceAccessPolicy(ResourceAccessPolicy):
    """Basic policy for resource access, enabling all accesses."""

    def __init__(self, groups: dict[str, str] | None = None, *, default_group: str = "primary"):
        super().__init__()

        self._groups = groups or {}
        self._default_group = default_group

    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return whether the given user can access the specified resource."""
        return {"read:all", "write:all"}

    def get_group_of_user(self, user_name: str) -> str:
        """Return the group this user is a part of."""
        return self._groups.get(user_name, self._default_group)
