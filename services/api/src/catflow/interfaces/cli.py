from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

import typer
import uvicorn
from dotenv import load_dotenv

from catflow.application.service import StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow.interfaces.api import AppSettings, create_app

app = typer.Typer(no_args_is_help=True)


def validate_loopback_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("CatFlow host must be a numeric loopback address") from exc
    if not address.is_loopback:
        raise ValueError("CatFlow host must be loopback-only")
    return host


@app.command()
def serve(
    port: int = typer.Option(8765, min=1024, max=65535),
) -> None:
    """Serve the CatFlow API and built Vue application on the loopback interface."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=True)

    spa_dist = project_root / "apps" / "web" / "dist"
    if not (spa_dist / "index.html").is_file():
        raise typer.BadParameter(
            "apps/web/dist is missing; run npm --workspace apps/web run build first"
        )

    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    repository.active_canon_profile_id()
    application = create_app(
        StudioService(repository),
        settings=AppSettings(
            csrf_token=secrets.token_urlsafe(32),
            allowed_origins=(
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ),
        ),
        media_store=LocalMediaStore(
            project_root / os.environ.get("CATFLOW_MEDIA_ROOT", "var/media")
        ),
        spa_dist=spa_dist,
    )
    application.state.database_engine = engine
    uvicorn.run(application, host=validate_loopback_host("127.0.0.1"), port=port)
