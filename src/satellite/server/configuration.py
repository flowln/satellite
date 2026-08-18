from argparse import ArgumentParser
from collections.abc import Sequence
import logging
import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from yaml import SafeLoader, YAMLError, load

logger = logging.getLogger("satellite.configuration")


_ARGUMENTS = {
    "config": (
        "--config",
        "Queue manager configuration file location",
        "QSERVER_CONFIG",
    ),
    "api-prefix": (
        "--api-prefix",
        "Prefix to add to all API endpoints.",
        "SATELLITE_API_PREFIX",
    ),
}


def parse_cli_arguments():
    """Parse command-line arguments, configuring environment variables for the options."""
    parser = ArgumentParser("satellite")

    for name, (arg_name, arg_desc, env_name) in _ARGUMENTS.items():
        parser.add_argument(arg_name, help=f"{arg_desc} (env: {env_name})", dest=name)

    args = parser.parse_known_args()[0]

    for argument_name, value in args.__dict__.items():
        if value is None:
            continue

        env_name = _ARGUMENTS[argument_name][2]
        os.environ[env_name] = value


class _ManagerAuthenticationProvider(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True, extra="ignore")

    provider_name: str = Field(alias="provider")
    """Human-friendly name of the authentication provider."""

    mode: Literal["password"] = Field(default="password")
    """Mode of authentication with this provider. Defaults to 'password' (send the user name and password)."""

    expiration_time: int | float = Field(default=900)
    """Time, in seconds, that a token remains valid after generating it. Defaults to 15 minutes."""

    refresh_expiration_time: int | float = Field(default=1800)
    """Time, in seconds, that a refresh token remains valid after generating it. Defaults to 30 minutes."""

    authenticator: str
    """Python class path for an authentication provider."""

    args: dict[str, Any] = Field(default={})
    """Extra arguments to send to the authenticator at instance creation."""

    @field_validator("authenticator")
    @classmethod
    def validate_authenticator(cls, value) -> str:
        from importlib.util import find_spec

        try:
            module_path = str(value).split(":")[0]
            if find_spec(module_path) is None:
                raise ValueError(f"Couldn't find module at '{module_path}'.")
        except Exception as exc:
            raise ValueError(f"Failed to parse '{repr(value)}' as a python class path.") from exc

        return value


class _ManagerAuthenticationSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True, extra="ignore")

    secret_keys: list[str] | None = Field(alias="secret_key", default=None)
    """
    Static API keys for authentication.

    In enclosed by ${...}, its value is taken from the corresponding environment variable.
    """

    allow_anonymous_access: bool = Field(default=False)
    """Allow public access without authentication. Defaults to False."""

    providers: list[_ManagerAuthenticationProvider] | None = Field(default=None)
    """List of authentication providers to use."""


class _ManagerAPIAuthorizationPolicy(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    policy_name: str = Field(alias="policy", default="satellite.server.security.access_policies:BasicAPIAccessPolicy")
    """Python class path of the access policy to use."""

    args: dict[str, Any] = Field(default={})
    """Extra arguments to send to the access policy at instance creation."""

    @field_validator("policy_name")
    @classmethod
    def validate_policy_name(cls, value) -> str:
        from importlib.util import find_spec

        try:
            module_path = str(value).split(":")[0]
            if find_spec(module_path) is None:
                raise ValueError(f"Couldn't find module at '{module_path}'.")
        except Exception as exc:
            raise ValueError(f"Failed to parse '{repr(value)}' as a python class path.") from exc

        return value


class _ManagerResourceGroupPermissions(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    allowed_plans: Sequence[str] | tuple[None] = Field(default=(None,))
    """List of plan names and patterns this group is allowed to use."""

    forbidden_plans: Sequence[str] | tuple[None] = Field(default=(None,))
    """List of plan names and patterns this group is forbidden to use."""

    allowed_devices: Sequence[str] | tuple[None] = Field(default=(None,))
    """(NOT IMPLEMENTED) List of device names and patterns this group is allowed to use."""

    forbidden_devices: Sequence[str] | tuple[None] = Field(default=(None,))
    """(NOT IMPLEMENTED) List of device names and patterns this group is forbidden to use."""

    def is_plan_allowed(self, plan_name: str) -> bool:
        """Return whether a plan is allowed or forbidden for this user."""

        def matches(expr: str) -> bool:
            if expr.startswith(":"):
                pattern = re.compile(expr[1:])
            else:
                pattern = re.compile("^" + expr + "$")

            return pattern.match(plan_name) is not None

        is_allowed = all(expr is None or matches(expr) for expr in self.allowed_plans)
        if not is_allowed:
            return False

        is_forbidden = any(expr is not None and matches(expr) for expr in self.forbidden_plans)
        if is_forbidden:
            return False

        return True


class _ManagerResourceAuthorizationPolicy(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    policy_name: str = Field(
        alias="policy", default="satellite.server.security.access_policies:BasicResourceAccessPolicy"
    )
    """Python class path of the access policy to use."""

    group_permissions: dict[str, _ManagerResourceGroupPermissions] = Field(alias="user_groups", default={})
    """Assignment of resource access permissions for individual groups."""

    args: dict[str, Any] = Field(default={})
    """Extra arguments to send to the access policy at instance creation."""

    @field_validator("policy_name")
    @classmethod
    def validate_policy_name(cls, value) -> str:
        from importlib.util import find_spec

        try:
            module_path = str(value).split(":")[0]
            if find_spec(module_path) is None:
                raise ValueError(f"Couldn't find module at '{module_path}'.")
        except Exception as exc:
            raise ValueError(f"Failed to parse '{repr(value)}' as a python class path.") from exc

        return value


class _ManagerAuthorizationSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True, extra="ignore")

    api_access_authorization: _ManagerAPIAuthorizationPolicy = Field(
        alias="api_access", default=_ManagerAPIAuthorizationPolicy()
    )
    """Access control policy for access of API endpoints."""

    resource_access_authorization: _ManagerResourceAuthorizationPolicy = Field(
        alias="resource_access", default=_ManagerResourceAuthorizationPolicy()
    )
    """Access control policy for access of server resources."""


class _ManagerNetworkSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore")

    persistence_backend: Literal["none", "redis"] = Field(default="redis", frozen=True)
    """Which method will be used to persist data across restarts."""
    use_mocked_backend: bool = Field(default=False)
    """Use a mocked backend instead of connecting to real infrastructure."""

    mock_arguments: dict[str, Any] = Field(exclude=True, default={})
    """Extra keyword arguments passed to a mocked backend. Used for testing purposes."""

    redis_address: str | None = Field(alias="redis_addr", default=None, frozen=True)
    """Network address on which to find a live Redis instance."""
    redis_name_prefix: str = Field(default="qs_default", frozen=True)
    """String prepended to all keys used by satellite (default: qs_default)."""
    redis_no_queue_name_in_key: bool = Field(default=False, frozen=True)
    """
    Do not append the queue name to the redis persistence key.

    This breaks multi-queue setups, but allows for easier compatibility with queueserver.
    """


class _ManagerOperationSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore", frozen=True)

    print_console_output: bool = Field(default=False)
    """Whether to print the output of the environment to the main process's stdout / stderr."""
    console_logging_level: Literal["VERBOSE", "NORMAL", "QUIET", "SILENT"] = Field(default="NORMAL")
    """Configure the logging module's logging level used. Corresponds to `logging.DEBUG` - `logging.ERROR`, in order."""

    emergency_key: str | None = Field(default=None)
    """Master key for unlocking the queue regardless of who locked it."""

    @property
    def actual_logging_level(self) -> int:
        match self.console_logging_level:
            case "VERBOSE":
                return logging.DEBUG
            case "NORMAL":
                return logging.INFO
            case "QUIET":
                return logging.WARNING
            case "SILENT":
                return logging.ERROR


class _ManagerStartupSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore", frozen=True)

    startup_directory: Path | None = Field(alias="startup_dir", default=None)
    """Directory path from with to load new environments."""


class ManagerConfiguration(BaseModel):
    """Base model for all application-wide configuration."""

    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore")

    authentication: _ManagerAuthenticationSection = Field(default=_ManagerAuthenticationSection())
    """Default 'authentication' section for all managers."""
    authorization: _ManagerAuthorizationSection = Field(default=_ManagerAuthorizationSection())
    """Default 'authorization' section for all managers."""
    network: _ManagerNetworkSection = Field(default=_ManagerNetworkSection())
    """Default 'network' section for all managers."""
    operation: _ManagerOperationSection = Field(default=_ManagerOperationSection())
    """Default 'operation' section for all managers."""
    startup: _ManagerStartupSection = Field(default=_ManagerStartupSection())
    """Default 'startup' section for all managers."""

    primary_manager: str = Field(default="queue")
    """Name of the manager that will respond to requests on the unprefixed endpoints."""

    managers: "dict[str, ManagerConfiguration]" = Field(default={})
    """Configuration specific to each manager. It takes priority over the default configurations."""


def _get_manager_configuration_location() -> Path:
    """Retrieve the manager configuration location for the current environment."""
    arg_name, _, env_name = _ARGUMENTS["config"]
    configured_path = os.environ.get(env_name, None)

    if configured_path is None:
        logger.error(
            "Failed to retrieve a valid configuration path for the queue manager."
            f" Consider setting the {arg_name} option or using the '{env_name}' environment variable."
        )

        raise RuntimeError

    path = Path(configured_path)

    if not path.exists():
        logger.error("The specified manager configuration path '%s' does not exist!", str(path))

        raise RuntimeError

    return path


class _YamlEnvironmentVariableLoader(SafeLoader):
    def construct_scalar(self, node):
        value = super().construct_scalar(node)

        if isinstance(value, str):
            pattern = r"\$\{([^{}]+)\}"  # parse: ${(...)} -> (...)

            matches = re.finditer(pattern, value)
            for match in matches:
                var_name = match.group(1)
                value = value.replace(match.group(0), os.environ.get(var_name, ""))

        return value


def load_manager_configuration() -> ManagerConfiguration:
    """
    Load configuration from a YAML configuration file.

    The configuration file is retrieved from the appropriate environment variable,
    which is also configured by command-line arguments if they are set.
    """
    configuration_path = _get_manager_configuration_location()

    try:
        with open(configuration_path) as _file:
            parsed_data = load(_file, _YamlEnvironmentVariableLoader)
    except YAMLError as exc:
        logger.exception(
            "Failed to parse file from '%s' as a valid YAML file:",
            str(configuration_path),
            exc_info=exc,
        )

        raise

    configuration = ManagerConfiguration.model_validate(parsed_data, by_alias=True)

    if len(configuration.managers) == 0:
        configuration.managers["queue"] = configuration.model_copy(deep=True)

    # Join default global options with manager-specific ones, giving priority to the latter
    for manager_name in configuration.managers.keys():
        configuration.managers[manager_name] = ManagerConfiguration.model_validate(
            configuration.model_dump(
                by_alias=True,
                exclude={
                    "managers",
                },
            )
            | parsed_data.get("managers", {}).get(manager_name, {}),
            by_alias=True,
        )

    return configuration
