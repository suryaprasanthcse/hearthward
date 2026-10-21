"""uvicorn entry point for the Fargate `mcp` task: `uvicorn --factory hearthward.mcp.asgi:create --port 8080`."""

from __future__ import annotations

from starlette.applications import Starlette

from hearthward.mcp.app import create_app_from_env


def create() -> Starlette:
    return create_app_from_env()
