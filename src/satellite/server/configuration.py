from argparse import ArgumentParser
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("satellite.configuration")


_ARGUMENTS = {
    "config": (
        "--config",
        "Queue manager configuration file location",
        "QSERVER_CONFIG",
    )
}


def parse_cli_arguments():
    """Parse command-line arguments, configuring environment variables for the options."""
    parser = ArgumentParser("satellite")

    for name, (arg_name, arg_desc, env_name) in _ARGUMENTS.items():
        parser.add_argument(arg_name, help=f"{arg_desc} (env: {env_name})", dest=name)

    args = parser.parse_args()

    for argument_name, value in args.__dict__.items():
        if value is None:
            continue

        env_name = _ARGUMENTS[argument_name][2]
        os.environ[env_name] = value


class _ManagerNetworkSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore")

    persistence_backend: Literal["none", "redis"] = Field(default="redis", frozen=True)
    """Which method will be used to persist data across restarts."""
    use_mocked_backend: bool = Field(default=False, frozen=True)
    """Use a mocked backend instead of connecting to real infrastructure."""

    mock_arguments: dict[str, Any] = Field(exclude=True, default={})
    """Extra keyword arguments passed to a mocked backend. Used for testing purposes."""

    redis_address: str | None = Field(alias="redis_addr", default=None, frozen=True)
    """Network address on which to find a live Redis instance."""
    redis_name_prefix: str = Field(default="qs_default", frozen=True)
    """String prepended to all keys used by satellite (default: qs_default)."""


class _ManagerOperationSection(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore", frozen=True)

    print_console_output: bool = Field(default=False)
    """Whether to print the output of the environment to the main process's stdout / stderr."""
    console_logging_level: Literal["VERBOSE", "NORMAL", "QUIET", "SILENT"] = Field(default="NORMAL")
    """Configure the logging module's logging level used. Corresponds to `logging.DEBUG` - `logging.ERROR`, in order."""

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


def load_manager_configuration() -> ManagerConfiguration:
    """
    Load configuration from a YAML configuration file.

    The configuration file is retrieved from the appropriate environment variable,
    which is also configured by command-line arguments if they are set.
    """
    from yaml import YAMLError, safe_load

    configuration_path = _get_manager_configuration_location()

    try:
        with open(configuration_path) as _file:
            parsed_data = safe_load(_file)
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
