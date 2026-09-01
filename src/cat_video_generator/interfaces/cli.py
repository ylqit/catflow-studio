"""Small operational CLI for the V5 Web studio."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
import uvicorn

from ..bootstrap import build_diagnostic_container, build_runtime_container
from ..config import RuntimeSettings, load_local_env
from ..infrastructure.db.session import ALEMBIC_HEAD
from .api import create_app
from .jobs import JobRegistry

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


@app.command()
def doctor() -> None:
    """Report database revision, Ark configuration and local media tools."""

    load_local_env()
    runtime = RuntimeSettings.from_env().preflight_report()
    container = None
    try:
        container = build_diagnostic_container()
        database_ready = container.alembic_revision == ALEMBIC_HEAD
        _echo(
            {
                "database": container.database_name,
                "alembicRevision": container.alembic_revision,
                "expectedAlembicRevision": ALEMBIC_HEAD,
                "ready": database_ready,
                "databaseReady": database_ready,
                "arkReady": runtime["arkReady"],
                "ffmpegAvailable": runtime["ffmpegAvailable"],
                "ffprobeAvailable": runtime["ffprobeAvailable"],
                "videoGenerationReady": runtime["videoGenerationReady"],
                "localCompositionReady": runtime["localCompositionReady"],
                "runtime": runtime,
            }
        )
    except Exception as exc:
        _echo(
            {
                "database": None,
                "alembicRevision": None,
                "expectedAlembicRevision": ALEMBIC_HEAD,
                "ready": False,
                "databaseReady": False,
                "databaseError": type(exc).__name__,
                "arkReady": runtime["arkReady"],
                "ffmpegAvailable": runtime["ffmpegAvailable"],
                "ffprobeAvailable": runtime["ffprobeAvailable"],
                "videoGenerationReady": runtime["videoGenerationReady"],
                "localCompositionReady": runtime["localCompositionReady"],
                "runtime": runtime,
            }
        )
        raise typer.Exit(code=1) from exc
    finally:
        if container is not None:
            container.close()


@app.command("api")
def serve_api(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    static_dir: Path | None = typer.Option(None, "--static-dir"),
    reload: bool = typer.Option(False, "--reload/--no-reload"),
) -> None:
    """Serve the API and, when supplied, the built Vue application."""

    if static_dir is not None:
        static_dir = static_dir.expanduser().resolve()
        if not (static_dir / "index.html").is_file():
            raise typer.BadParameter(f"static directory has no index.html: {static_dir}")
    if reload:
        if static_dir is not None:
            raise typer.BadParameter("--reload cannot be combined with --static-dir")
        uvicorn.run(
            "cat_video_generator.interfaces.cli:create_runtime_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
        return

    web_app = create_runtime_app(static_dir=static_dir)
    uvicorn.run(web_app, host=host, port=port)


def create_runtime_app(*, static_dir: Path | None = None):
    """Own one API process' runtime resources and shutdown lifecycle."""

    container = build_runtime_container()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="shot-queue")
    jobs = JobRegistry(executor=executor)
    web_app = create_app(container, job_registry=jobs, static_dir=static_dir)

    @web_app.on_event("shutdown")
    def close_resources() -> None:
        executor.shutdown(wait=False, cancel_futures=False)
        container.close()

    return web_app


@app.command("canon-repair")
def repair_canon(
    source_dir: Path = typer.Option(
        Path("风格定稿/Canon-v1"),
        "--source-dir",
        file_okay=False,
        dir_okay=True,
        exists=True,
    ),
) -> None:
    """Validate and relink the approved Canon set into the active asset root."""

    container = build_runtime_container()
    try:
        repaired = container.canon.repair_manifest(source_dir / "manifest.json")
        _echo(
            {
                "repaired": len(repaired),
                "assets": [
                    {
                        "id": str(item.id),
                        "semanticKey": item.semantic_key,
                        "sha256": item.sha256,
                        "path": None if item.path is None else str(item.path),
                    }
                    for item in repaired
                ],
            }
        )
    finally:
        container.close()


@app.command("canon-install")
def install_canon(
    source_dir: Path = typer.Option(
        Path("风格定稿/Canon-v4"),
        "--source-dir",
        file_okay=False,
        dir_okay=True,
        exists=True,
    ),
) -> None:
    """Install a new immutable Canon manifest without overwriting existing semantic keys."""

    container = build_runtime_container()
    try:
        installed = container.canon.install_manifest(source_dir / "manifest.json")
        _echo(
            {
                "installed": len(installed),
                "assets": [
                    {
                        "id": str(item.id),
                        "semanticKey": item.semantic_key,
                        "sha256": item.sha256,
                        "path": None if item.path is None else str(item.path),
                    }
                    for item in installed
                ],
            }
        )
    finally:
        container.close()


def _echo(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
