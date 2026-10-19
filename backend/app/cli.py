"""Operational commands: `uv run claimsaathi --help`."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

import typer

from app.api.container import build_container
from app.config import runtime_settings
from app.db.models import utcnow
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


evals = typer.Typer(no_args_is_help=True, help="Synthetic cases with known answers.")
app.add_typer(evals, name="eval")


@evals.command("generate")
def generate_cases(
    out: Annotated[Path, typer.Option(help="Folder for the PDFs and manifest.json.")],
    cases: Annotated[int, typer.Option(min=1, help="Cases; scenarios rotate.")] = 28,
    seed: Annotated[int, typer.Option(help="Same seed, same dataset.")] = 7,
) -> None:
    """Write synthetic policies, bills, discharge summaries and insurer letters."""
    from app.evals.synthetic import generate

    typer.echo(f"wrote {generate(out, cases=cases, seed=seed)}")


@evals.command("run")
def run_eval(
    data: Annotated[Path, typer.Option(help="Folder made by `eval generate`.")],
    report: Annotated[Path, typer.Option(help="Folder for report.json and report.md.")],
    limit: Annotated[int | None, typer.Option(min=1, help="Score only the first N cases.")] = None,
    chat: Annotated[bool, typer.Option(help="Include the multi-turn chat questions.")] = True,
    scenario: Annotated[
        list[str] | None, typer.Option(help="Only these scenarios; repeat the option.")
    ] = None,
    pause: Annotated[
        float, typer.Option(min=0, help="Seconds between cases, to stay inside free quotas.")
    ] = 0.0,
) -> None:
    """Run every case through the full pipeline and score it against the truth."""
    # The eval uses its own database and vector store, never the app's.
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(data / 'eval.db').as_posix()}"
    os.environ["QDRANT_PATH"] = str(data / "qdrant")
    os.environ.pop("QDRANT_URL", None)
    os.environ["QDRANT_COLLECTION"] = "eval_clauses"
    os.environ["CASE_TTL_HOURS"] = "6"

    from app.evals.runner import EvalRunner, render_markdown, summarise
    from app.evals.synthetic import load_manifest

    cases = [
        c for c in load_manifest(data / "manifest.json") if not scenario or c.scenario in scenario
    ]
    cases = cases[:limit]

    async def run() -> dict[str, object]:
        container = build_container()
        runner = EvalRunner(container, data)
        try:
            await runner.prepare()
            started = utcnow()
            scores = []
            for number, case in enumerate(cases, start=1):
                score = await runner.run_case(case, chat=chat)
                outcome = score.error or f"payout ok={score.payout_ok}"
                typer.echo(f"[{number}/{len(cases)}] {case.case_id}: {outcome}")
                scores.append(score)
                if pause and number < len(cases):
                    await asyncio.sleep(pause)
            return summarise(scores, await runner.model_usage(started))
        finally:
            await container.close()

    summary = asyncio.run(run())
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (report / "report.md").write_text(render_markdown(summary), encoding="utf-8")
    typer.echo(render_markdown(summary))
