import json
import logging

import typer
from alembic import command
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from modelbridge import __version__
from modelbridge.application.authentication import AuthenticationService
from modelbridge.application.workspaces import WorkspaceService
from modelbridge.domain.access import ResourceConflict
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import (
    Database,
    create_metadata_engine,
    migration_config,
    require_schema,
)

app = typer.Typer(no_args_is_help=True, help="ModelBridge development administration.")
db = typer.Typer(no_args_is_help=True)
app.add_typer(db, name="db")


@app.command()
def registration_code(email: str = typer.Option(..., "--email")) -> None:
    """Issue one-hour email-bound proof; deliver privately to its verified recipient."""
    try:
        settings = Settings()
        engine = create_metadata_engine(settings)
        try:
            require_schema(engine)
            code = AuthenticationService(Database(engine), settings).issue_registration_code(email)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValidationError, RuntimeError, ValueError):
        typer.echo("Registration-code issuance failed.", err=True)
        raise typer.Exit(1) from None
    typer.echo("Deliver privately to the verified email recipient; expires in one hour:")
    typer.echo(code)


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def keygen() -> None:
    """Generate an encryption key; store the output in a secret manager."""
    typer.echo(Fernet.generate_key().decode("ascii"))


@db.command()
def upgrade() -> None:
    """Apply reviewed metadata migrations; back up before upgrading."""
    logging.getLogger("alembic").setLevel(logging.WARNING)
    try:
        command.upgrade(migration_config(), "head")
    except (SQLAlchemyError, ValidationError):
        typer.echo("Metadata upgrade failed; check configuration and connectivity.", err=True)
        raise typer.Exit(1) from None
    typer.echo("Metadata schema upgraded.")


@app.command()
def bootstrap(name: str = typer.Option(..., "--name")) -> None:
    """Create a workspace and display its new owner credential exactly once."""
    try:
        engine = create_metadata_engine(Settings())
        try:
            require_schema(engine)
            workspace, token = WorkspaceService(Database(engine)).bootstrap(name)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValidationError, RuntimeError, ResourceConflict, ValueError):
        typer.echo("Bootstrap failed; check schema, configuration and workspace name.", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Workspace: {workspace.id}")
    typer.echo("Store this owner credential securely; it will not be shown again:")
    typer.echo(token)


@app.command()
def ready() -> None:
    """Check metadata schema and worker heartbeat."""
    try:
        settings = Settings()
        engine = create_metadata_engine(settings)
        try:
            checks = Database(engine).readiness(settings.worker_id, settings.worker_stale_seconds)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValidationError):
        typer.echo('{"status":"not_ready"}')
        raise typer.Exit(1) from None
    typer.echo(json.dumps(checks))
    if not all(checks.values()):
        raise typer.Exit(1)
