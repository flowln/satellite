from collections import defaultdict
import os

from fastapi.security import APIKeyHeader, APIKeyQuery, OAuth2PasswordBearer

### Authorization

API_SCOPES = {
    "read:status": "Read access to the queue's current status.",
    "read:queue": "Read access to the queue's queued items.",
    "read:history": "Read access to the queue's items in history.",
    "read:console": "Read access to the queue's console output.",
    "write:manager:control": "Access to manager controls, like changing the environment state.",
    "write:plan:control": "Access to controls of the running plan's execution.",
    "write:queue:edit": "Write access to edit the queued items.",
    "write:history:edit": "Write access to edit the items in the history.",
}
"""Available API scopes for controlling access to the server."""

### Authentication

# Single-user

ANONYMOUS_USER_NAME = "unauthenticated_public"

API_KEY_QUERY = APIKeyQuery(name="api-key", auto_error=False)
API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)

# Multi-user

# FIXME: This should remain consistent across restarts
JWT_SECRET_KEY = os.urandom(256)
JWT_ALGORITHM = "HS256"

JWT_REFRESH_CLAIM = "refresh"
JWT_PROVIDER_CLAIM = "provider"
JWT_SALT_CLAIM = "salt"
"""Used for providing uniqueness for each token, even if all parameters are equal."""
JWT_API_SCOPES_CLAIM = "scopes"

# TODO: Periodically clean up this. Any expired token can be removed from the blacklist.
JWT_BLACKLIST: dict[str, set] = defaultdict(set)

PASSWORD_SCHEME = OAuth2PasswordBearer(
    tokenUrl="login", refreshUrl="session_refresh", scopes=API_SCOPES, auto_error=False
)

### Reimports

from .main import authenticate_dependencies, get_current_user, get_current_user_group  # noqa
