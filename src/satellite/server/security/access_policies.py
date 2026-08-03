from abc import abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from satellite.server.security import ANONYMOUS_USER_NAME, API_SCOPES

ScopeOperation = Literal["scopes_set", "scopes_add", "scopes_remove"]
type BasicAPIRolesType = dict[str, dict[ScopeOperation, Sequence[str]]]


class APIAccessPolicy:
    """Base class for API access policies."""

    @abstractmethod
    def get_scopes_for_user(self, user_name: str) -> set[str]:
        """Return whether the given user has access to the specified scope."""
        raise NotImplementedError


class BasicAPIAccessPolicy:
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
        """Return whether the given user has access to the specified scope."""
        return self._roles.get(user_name, set())

    @classmethod
    def get_roles_for_admin_power(cls, *users: *str) -> BasicAPIRolesType:
        """Return an argument to BasicAPIAccessPolicy in which all provided users have all permissions."""
        return {user: {"scopes_set": list(API_SCOPES.keys())} for user in users}


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
