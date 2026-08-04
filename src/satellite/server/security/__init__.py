from collections import defaultdict
from datetime import UTC, datetime, timedelta
import importlib
import logging
import os
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery, OAuth2PasswordBearer, OAuth2PasswordRequestForm, SecurityScopes
import jwt

from ...models import SuccessfulLoginResponse, UserInformation
from ..configuration import (
    ManagerConfiguration,
    _ManagerAuthenticationProvider,
    load_manager_configuration,
)

logger = logging.getLogger("satellite.server.security")

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


def _get_configuration() -> ManagerConfiguration:
    return load_manager_configuration()


def _get_secret_keys(configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)]) -> list[str] | None:
    return configuration.authentication.secret_keys


def _check_api_key(
    _secret_keys: Annotated[list[str] | None, Depends(_get_secret_keys)],
    api_key_query: Annotated[str, Security(API_KEY_QUERY)],
    api_key_header: Annotated[str, Security(API_KEY_HEADER)],
) -> bool:
    if _secret_keys is None:
        return False

    logger.debug("Checking API Key...")
    if api_key_query in _secret_keys:
        return True
    if api_key_header in _secret_keys:
        return True
    return False


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


def _create_tokens_for_providers(
    user_name: str,
    authentication_provider: _ManagerAuthenticationProvider,
    api_authorizer: Any,
    override_expiration_time: int | float | None = None,
    override_refresh_expiration_time: int | float | None = None,
) -> tuple[str, int | float, str, int | float]:
    if override_expiration_time is None:
        override_expiration_time = authentication_provider.expiration_time
    else:
        override_expiration_time = min(override_expiration_time, authentication_provider.expiration_time)
    expiration_time = timedelta(seconds=override_expiration_time)

    if override_refresh_expiration_time is None:
        override_refresh_expiration_time = authentication_provider.refresh_expiration_time
    else:
        override_refresh_expiration_time = min(
            override_refresh_expiration_time, authentication_provider.refresh_expiration_time
        )
    refresh_expiration_time = timedelta(seconds=override_refresh_expiration_time)

    api_scopes = api_authorizer.get_scopes_for_user(user_name)

    token = _get_access_token(
        user_name,
        expiration_time,
        {
            JWT_REFRESH_CLAIM: False,
            JWT_PROVIDER_CLAIM: authentication_provider.provider_name,
            JWT_SALT_CLAIM: int.from_bytes(os.urandom(8)),
            JWT_API_SCOPES_CLAIM: list(api_scopes),
        },
    )
    refresh_token = _get_access_token(
        user_name,
        refresh_expiration_time,
        {
            JWT_REFRESH_CLAIM: True,
            JWT_PROVIDER_CLAIM: authentication_provider.provider_name,
            JWT_SALT_CLAIM: int.from_bytes(os.urandom(8)),
            JWT_API_SCOPES_CLAIM: [],
        },
    )

    return token, expiration_time.total_seconds(), refresh_token, refresh_expiration_time.total_seconds()


def _get_decoded_token(
    token: Annotated[str | None, Depends(PASSWORD_SCHEME)],
    configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)],
) -> dict[str, Any] | str | None:
    """
    Parse a raw token into its payload (claims).

    Arguments
    ---------
    token : str
        The raw JWT token.
    configuration : ManagerConfiguration
        The current server configuration.

    Raises
    ------
    HTTPException
        If the configuration doesn't allow for anonymous access, and the token is not valid.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the claims this token contains. An extra key is added ('raw_token')
        with the original raw token string.
    str
        If the token is invalid for some reason, and anonymous access is allowed, the reason for the
        token being invalid is returned.
    """
    if token is None:
        return

    def _raise_exception_if_needed(extra_msg: str, previous_exc: Exception | None = None):
        if not configuration.authentication.allow_anonymous_access:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not validate credentials: {extra_msg}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from previous_exc
        return extra_msg

    try:
        payload = jwt.decode(
            token,
            key=JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": True, "verify_signature": True},
        )
        payload["raw_token"] = token
        return payload
    except jwt.InvalidSignatureError as exc:
        logger.warning("Received token with invalid signature: '%s'.", token)

        return _raise_exception_if_needed("Token has invalid signature.", exc)
    except jwt.ExpiredSignatureError as exc:
        logger.warning("Received expired token: '%s'.", token)

        return _raise_exception_if_needed("Token has already expired.", exc)
    except jwt.InvalidTokenError as exc:
        return _raise_exception_if_needed("Failed to validate token.", exc)


def _get_provider_by_name(provider_name: str, configuration: ManagerConfiguration) -> _ManagerAuthenticationProvider:
    if configuration.authentication.providers is None:
        raise RuntimeError

    for provider in configuration.authentication.providers:
        if provider.provider_name == provider_name:
            return provider

    raise RuntimeError


def _create_password_login_handler(
    router: APIRouter,
    authentication_provider: _ManagerAuthenticationProvider,
    api_authorizer,
) -> APIRouter:
    module_path, class_name = authentication_provider.authenticator.split(":")
    authenticator_cls = getattr(importlib.import_module(module_path), class_name)
    authenticator = authenticator_cls(**authentication_provider.args)

    @router.post("/login")
    async def login(
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        override_expiration_time: int | float | None = None,
        override_refresh_expiration_time: int | float | None = None,
    ) -> SuccessfulLoginResponse:
        """
        Check credentials and, if authenticated, return tokens for using the API.

        Parameters
        ----------
        form_data : OAuth2PasswordRequestForm
            Form information containing the username and password with which to authenticate.
        override_expiration_time : int or float, optional
            Set a lower expiration time than the provider's default. Cannot be higher than the default.
        override_refresh_expiration_time : int or float, optional
            Set a lower refresh expiration time than the provider's default. Cannot be higher than the default.
        """
        username = form_data.username
        password = form_data.password

        if not authenticator.authenticate_with_password(username, password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect username or password")

        token, token_expires_in, refresh_token, _ = _create_tokens_for_providers(
            username,
            authentication_provider,
            api_authorizer,
            override_expiration_time,
            override_refresh_expiration_time,
        )

        return SuccessfulLoginResponse(
            token=token, refresh_token=refresh_token, expires_in=token_expires_in, token_type="bearer"
        )

    @router.post("/logout")
    async def logout(token: Annotated[dict[str, Any] | str, Depends(_get_decoded_token)]) -> bool:
        """Revoke permissions from the given token, making it unusable."""
        if isinstance(token, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Credentials are not valid: {token}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        global JWT_BLACKLIST
        JWT_BLACKLIST[token[JWT_PROVIDER_CLAIM]].add(token["raw_token"])

        return True

    @router.post("/session_refresh")
    async def session_refresh(
        token: Annotated[dict[str, Any] | str, Depends(_get_decoded_token)],
        override_expiration_time: int | float | None = None,
        override_refresh_expiration_time: int | float | None = None,
    ) -> SuccessfulLoginResponse:
        """
        Use a valid refresh token to generate new valid access and refresh tokens.

        Parameters
        ----------
        token : dict[str, Any] or str
            The parsed and valid JWT refresh token to use.
        override_expiration_time : int or float, optional
            Set a lower expiration time than the provider's default. Cannot be higher than the default.
        override_refresh_expiration_time : int or float, optional
            Set a lower refresh expiration time than the provider's default. Cannot be higher than the default.
        """
        global JWT_BLACKLIST

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

        provider_name = token[JWT_PROVIDER_CLAIM]
        if token["raw_token"] in JWT_BLACKLIST[provider_name]:
            logger.critical("Attempted usage of revoked refresh token: '%s' for user '%s'.", token, token["sub"])

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This token has been revoked. This incident will be reported.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Invalidate this refresh token.
        JWT_BLACKLIST[token[JWT_PROVIDER_CLAIM]].add(token["raw_token"])

        user_name = token["sub"]
        logger.info("Refreshing tokens for user '%s'.", user_name)

        token, token_expires_in, refresh_token, _ = _create_tokens_for_providers(
            user_name,
            authentication_provider,
            api_authorizer,
            override_expiration_time,
            override_refresh_expiration_time,
        )
        return SuccessfulLoginResponse(
            token=token, refresh_token=refresh_token, expires_in=token_expires_in, token_type="bearer"
        )

    return router


def get_current_user(
    has_valid_api_key: Annotated[bool, Depends(_check_api_key)],
    token: Annotated[dict[str, Any] | str | None, Depends(_get_decoded_token)],
    security_scopes: SecurityScopes,
    configuration: Annotated[ManagerConfiguration, Depends(_get_configuration)],
) -> str:
    """FastAPI Dependency for validating a token and returning the user of that token."""
    if security_scopes:
        auth_error_headers = {"WWW-Authenticate": f'Bearer scope="{security_scopes.scope_str}"'}
    else:
        auth_error_headers = {"WWW-Authenticate": "Bearer"}

    # NOTE: str -> invalid token, None -> no token provided
    if isinstance(token, (str, type(None))):
        if has_valid_api_key:
            return ANONYMOUS_USER_NAME

        if configuration.authentication.allow_anonymous_access:
            return ANONYMOUS_USER_NAME

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers=auth_error_headers,
        )

    if token.get(JWT_REFRESH_CLAIM, False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot use a refresh token for accessing resources.",
            headers=auth_error_headers,
        )

    provider_name = token[JWT_PROVIDER_CLAIM]
    if token["raw_token"] in JWT_BLACKLIST[provider_name]:
        logger.critical("Attempted usage of revoked access token: '%s' for user '%s'.", token, token["sub"])

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This token has been revoked. This incident will be reported.",
            headers=auth_error_headers,
        )

    token_scopes = token.get(JWT_API_SCOPES_CLAIM, set())
    if any(scope not in token_scopes for scope in security_scopes.scopes):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not enough permissions to access this endpoint.",
            headers=auth_error_headers,
        )

    return token["sub"]


def authenticate_dependencies() -> APIRouter:
    """
    Return an APIRouter with authentication-specific endpoints.

    The basic implementation consists of a set of dependencies, that automatically validate headers and query parameters
    of endpoints for the needed authentication, and the API routes that allow the user to interact with that
    authentication, in the case of token-based logins.

    A more advanced use is to obtain the user associated with a token in a particular endpoint. For that, use the
    'get_current_user' dependency in the endpoint.
    """
    configuration = _get_configuration()

    router = APIRouter()

    api_authorization_provider = configuration.authorization.api_access_authorization
    module_path, class_name = api_authorization_provider.policy_name.split(":")
    api_authorizer_cls = getattr(importlib.import_module(module_path), class_name)
    api_authorizer = api_authorizer_cls(**api_authorization_provider.args)

    _keys = configuration.authentication.secret_keys
    if _keys is not None and any(len(x) != 0 for x in _keys):
        logger.debug("Using API Key authentication.")

    if configuration.authentication.providers is not None:
        if len(configuration.authentication.providers) > 1:
            logger.error("Received more than one authentication provider, which is not currently handled.")

        for provider in configuration.authentication.providers:
            if provider.mode == "password":
                logger.debug("Using token-based authentication via password.")

                router = _create_password_login_handler(
                    router,
                    provider,
                    api_authorizer,
                )

            break

    @router.get("/whoami")
    async def whoami(current_user: Annotated[str, Depends(get_current_user)]) -> UserInformation:
        """Query information about the user authenticated with the used token."""
        scopes_for_user = list(api_authorizer.get_scopes_for_user(current_user))
        return UserInformation(user_name=current_user, scopes=scopes_for_user)

    return router
