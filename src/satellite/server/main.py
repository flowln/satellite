import logging
import logging.config
from typing import Any

import click
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from satellite.server.console import (
    close_global_log_centralizer,
    get_global_log_centralizer,
)
from satellite.utils import docstring_numpy_to_markdown

from .configuration import (
    load_manager_configuration,
    parse_cli_arguments,
)
from .queue_manager import QueueManager


def _create_app(*, mock_arguments: dict[str, Any] | None = None) -> FastAPI:
    close_global_log_centralizer()

    central_logger = get_global_log_centralizer("Satellite - Console")
    central_logger.serve()

    logging.config.dictConfig(
        {
            "version": 1,
            # This needs to be False when testing, so that the already existing
            # loggers do not get disabled, and further tests get affected.
            "disable_existing_loggers": False,
            "formatters": {
                "full": {
                    "format": "[%(asctime)s %(levelname)s - %(name)s] %(message)s",
                },
            },
            "handlers": {
                "stream-full": {
                    "class": "logging.StreamHandler",
                    "formatter": "full",
                    "level": "DEBUG",
                },
            },
            "loggers": {
                "": {
                    "handlers": ["stream-full"],
                    "level": logging.INFO,
                },
                "uvicorn": {
                    "handlers": ["stream-full"],
                    "level": logging.INFO,
                    "propagate": False,
                },
                "httpx": {
                    "handlers": ["stream-full"],
                    "level": logging.WARNING,
                    "propagate": False,
                },
                "satellite.server": {
                    "handlers": ["stream-full"],
                    "level": logging.DEBUG,
                    "propagate": False,
                },
            },
        }
    )

    manager_config = load_manager_configuration()
    manager_config.network.mock_arguments.update(mock_arguments or {})

    openapi_tags = [
        {
            "name": "General",
            "description": "Operations on the entire satellite server.",
        },
    ]

    for manager_name in manager_config.managers.keys():
        openapi_tags.append(
            {
                "name": manager_name,
                "description": f"Operations on the '{manager_name}' queue.",
            },
        )

    app = FastAPI(openapi_tags=openapi_tags)

    @app.get("/", include_in_schema=False)
    @app.get("/ping", tags=["General"])
    async def ping():
        """Test connectivity with the server. Always responds 'pong'."""
        return {"message": "pong"}

    formatter = logging.getLogger("satellite.server").handlers[0].formatter
    for manager_name, configuration in manager_config.managers.items():
        # Configure logging / console
        logger = logging.getLogger(f"satellite.{manager_name}")
        logger.setLevel(manager_config.operation.actual_logging_level)

        central_logger.add_handler_to_logger(
            manager_name,
            logger,
            formatter,
            level=manager_config.operation.actual_logging_level,
        )

        configuration.network.mock_arguments.update(mock_arguments or {})

        manager = QueueManager(manager_name, configuration)
        router = manager.get_router()

        app.include_router(router, tags=[manager_name], prefix=f"/{manager_name}")
        if manager_name == manager_config.primary_manager:
            app.include_router(router)

    def _process_openapi_descriptions() -> dict[str, Any]:
        """Convert NumpyDoc-style endpoint descriptions into Markdown, so Swagger and Redoc can render it."""
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )

        for endpoint_name, endpoint in openapi_schema.get("paths", {}).items():
            used_verb = None
            for verb in {"get", "post"}:
                if verb in endpoint:
                    description = endpoint[verb].get("description", "")

                    used_verb = verb
                    break

            if used_verb is None:
                continue

            markdown_description = docstring_numpy_to_markdown(description)
            openapi_schema["paths"][endpoint_name][used_verb]["description"] = markdown_description

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = _process_openapi_descriptions  # ty: ignore

    return app


@click.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.pass_context
def entrypoint(ctx):  # noqa
    parse_cli_arguments()
    app = _create_app()

    import uvicorn

    ctx.forward(uvicorn.main, app=app)

    close_global_log_centralizer()
