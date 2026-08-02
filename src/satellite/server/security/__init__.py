from datetime import UTC, datetime, timedelta
import importlib
import logging
import ssl
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery, OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt

from ...models import SuccessfulLoginResponse, UserInformation
from ..configuration import ManagerConfiguration, _ManagerAuthenticationProvider, load_manager_configuration

logger = logging.getLogger("satellite.server.security")

ANONYMOUS_USER_NAME = "unauthenticated_default"

# Single-user

API_KEY_QUERY = APIKeyQuery(name="api-key", auto_error=False)
API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)

# Multi-user

# FIXME: This should remain consistent across restarts
JWT_SECRET_KEY = ssl.RAND_bytes(256)
JWT_ALGORITHM = "HS256"

JWT_REFRESH_CLAIM = "refresh"

PASSWORD_SCHEME = OAuth2PasswordBearer(tokenUrl="login", refreshUrl="session_refresh", auto_error=False)


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


# Probably not exaustive. Ref.: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
JWT_CLAIMS = Literal["iss", "sub", "aud", "nbf", "exp", "iat", "jti"]


def _get_access_token(username: str, expires_in: timedelta, claims: dict[JWT_CLAIMS | str, Any] | None = None) -> str:
    to_encode = (claims or {}).copy()

    to_encode["sub"] = username

    expire_moment = datetime.now(UTC) + expires_in
    to_encode["exp"] = expire_moment

    logger.info("Generating token for '%s' that will expire in %d seconds.", username, expires_in.total_seconds())

    encoded_jwt = jwt.encode(cast(dict, to_encode), JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    return encoded_jwt


def _create_tokens_for_provider(user_name: str, provider: _ManagerAuthenticationProvider) -> tuple[str, str]:
    expiration_time = timedelta(seconds=provider.expiration_time)
    refresh_expiration_time = timedelta(seconds=provider.refresh_expiration_time)

    token = _get_access_token(user_name, expiration_time)
    refresh_token = _get_access_token(user_name, refresh_expiration_time, {JWT_REFRESH_CLAIM: True})

    return token, refresh_token


def _create_password_login_handler(
    router: APIRouter,
    provider: _ManagerAuthenticationProvider,
) -> APIRouter:
    module_path, class_name = provider.authenticator.split(":")
    authenticator_cls = getattr(importlib.import_module(module_path), class_name)
    authenticator = authenticator_cls(**provider.args)

    @router.post("/login")
    async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> SuccessfulLoginResponse:
        username = form_data.username
        password = form_data.password

        if not authenticator.authenticate_with_password(username, password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect username or password")

        token, refresh_token = _create_tokens_for_provider(username, provider)
        return SuccessfulLoginResponse(
            token=token, refresh_token=refresh_token, expires_in=provider.expiration_time, token_type="bearer"
        )

    @router.post("/session_refresh")
    async def session_refresh(
        token: Annotated[dict[str, Any] | str, Depends(_get_decoded_token)],
        configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)],
    ) -> SuccessfulLoginResponse:
        if isinstance(token, str):
            logger.critical(
                "Someone attempted to use an expired token to refresh the session. This can be indicative of an attack."
            )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not validate credentials: {token}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not token.get(JWT_REFRESH_CLAIM, False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Received token doesn't have the required '{JWT_REFRESH_CLAIM}' claim.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if configuration.authentication.providers is None:
            raise RuntimeError

        user_name = token["sub"]
        logger.info("Refreshing tokens for user '%s'.", user_name)

        token, refresh_token = _create_tokens_for_provider(user_name, provider)
        return SuccessfulLoginResponse(
            token=token, refresh_token=refresh_token, expires_in=provider.expiration_time, token_type="bearer"
        )

    @router.get("/whoami")
    async def whoami(current_user: Annotated[str, Depends(get_current_user)]) -> UserInformation:
        return UserInformation(user_name=current_user)

    return router


def _get_decoded_token(
    token: Annotated[str, Depends(PASSWORD_SCHEME)],
    configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)],
) -> dict[str, Any] | str:
    def _raise_exception_if_needed(extra_msg: str, previous_exc: Exception | None = None):
        if not configuration.authentication.allow_anonymous_access:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not validate credentials: {extra_msg}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from previous_exc
        return extra_msg

    try:
        return jwt.decode(
            token,
            key=JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": True, "verify_signature": True},
        )
    except jwt.InvalidSignatureError as exc:
        logger.warning("Received token with invalid signature: '%s'.", token)

        return _raise_exception_if_needed("Token has invalid signature.", exc)
    except jwt.ExpiredSignatureError as exc:
        logger.warning("Received expired token: '%s'.", token)

        return _raise_exception_if_needed("Token has already expired.", exc)
    except jwt.InvalidTokenError as exc:
        return _raise_exception_if_needed("Failed to validate token.", exc)


def get_current_user(
    token: Annotated[dict[str, Any] | str, Depends(_get_decoded_token)],
) -> str:
    """FastAPI Dependency for validating a token and returning the user of that token."""
    if isinstance(token, str):
        return ANONYMOUS_USER_NAME

    username = token["sub"]
    return username


def authenticate_dependencies() -> tuple[list, APIRouter]:
    """
    Return a list of available authentication methods, along with a APIRouter with authentication-specific endpoints.

    The basic implementation consists of a set of dependencies, that automatically validate headers and query parameters
    of endpoints for the needed authentication, and the API routes that allow the user to interact with that
    authentication, in the case of token-based logins.

    A more advanced use is to obtain the user associated with a token in a particular endpoint. For that, use the
    'get_current_user' dependency in the endpoint.
    """
    configuration = _get_configuration()

    router = APIRouter()
    dependencies = []

    _keys = configuration.authentication.secret_keys
    if _keys is not None and any(len(x) != 0 for x in _keys):
        logger.debug("Using API Key authentication.")
        dependencies.append(Security(_check_api_key))

    if configuration.authentication.providers is not None:
        if len(configuration.authentication.providers) > 1:
            logger.error("Received more than one authentication provider, which is not currently handled.")

        for provider in configuration.authentication.providers:
            if provider.mode == "password":
                logger.debug("Using token-based authentication via password.")
                dependencies.append(Security(get_current_user))

                router = _create_password_login_handler(router, provider)

            break

    if len(dependencies) == 0:
        logger.warning("No authentication method has been set up.")

    return dependencies, router
