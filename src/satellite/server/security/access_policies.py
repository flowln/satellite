from abc import abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal, NamedTuple, TypedDict

from satellite.server.security import ANONYMOUS_USER_NAME, API_SCOPES

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


class ResourceAccessPolicy:
    """Base class for resource access policies."""

    @abstractmethod
    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return whether the given user can access the specified resource."""
        raise NotImplementedError


# TODO
class BasicResourceAccessPolicy:
    """Basic policy for resource access, enabling all accesses."""

    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return whether the given user can access the specified resource."""
        return {"read:all", "write:all"}
