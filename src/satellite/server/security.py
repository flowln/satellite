import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery

from .configuration import ManagerConfiguration, load_manager_configuration

logger = logging.getLogger("satellite.server.security")

API_KEY_QUERY = APIKeyQuery(name="api-key", auto_error=False)
API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)


def _get_configuration() -> ManagerConfiguration:
    return load_manager_configuration()


def _get_secret_keys(configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)]) -> list[str] | None:
    return configuration.authentication.secret_keys


def _check_api_key(
    _secret_keys: Annotated[list[str] | None, Depends(_get_secret_keys)],
    api_key_query: Annotated[str, Security(API_KEY_QUERY)],
    api_key_header: Annotated[str, Security(API_KEY_HEADER)],
    configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)],
) -> bool:
    if _secret_keys is None:
        return False

    logger.debug("Checking API Key...")
    if api_key_query in _secret_keys:
        return True
    if api_key_header in _secret_keys:
        return True

    if configuration.authentication.allow_anonymous_access:
        return False

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
    )


def authenticate_dependencies() -> list:
    """Return a list of available authentication methods, as FastAPI dependencies."""
    configuration = _get_configuration()

    dependencies = []

    _keys = configuration.authentication.secret_keys
    if _keys is not None and any(len(x) != 0 for x in _keys):
        logger.debug("Using API Key authentication.")
        dependencies.append(Security(_check_api_key))

    if len(dependencies) == 0:
        logger.warning("No authentication method has been set up.")

    return dependencies
