"""Operational commands: `uv run claimsaathi --help`."""

import asyncio

import typer

from app.api.container import build_container
from app.config import runtime_settings
from app.observability import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _setup() -> None:
    configure_logging(runtime_settings())


@app.command()
def purge() -> None:
    """Delete expired cases, their documents and their policy vectors."""

    async def run() -> int:
        container = build_container()
        try:
            return await container.cases.purge_expired()
        finally:
            await container.close()

    typer.echo(f"purged {asyncio.run(run())} cases")


@app.command()
def routes() -> None:
    """Show which models each route will try, in order, given the keys configured."""

    async def run() -> dict[str, list[str]]:
        container = build_container()
        try:
            return container.router.summary()
        finally:
            await container.close()

    for task, chain in asyncio.run(run()).items():
        typer.echo(f"{task}: {' -> '.join(chain) or '(no provider enabled)'}")
