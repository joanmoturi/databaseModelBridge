import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine
from typer.testing import CliRunner

from modelbridge import __version__
from modelbridge.application.workspaces import WorkspaceService
from modelbridge.cli import app
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database

runner = CliRunner()


def test_version_and_key_generation_need_no_database_configuration() -> None:
    assert runner.invoke(app, ["version"]).stdout.strip() == __version__
    generated = runner.invoke(app, ["keygen"])
    assert generated.exit_code == 0
    Fernet(generated.stdout.strip().encode())


@pytest.mark.integration
def test_cli_bootstrap_creates_usable_owner_and_fails_duplicates(
    postgres: tuple[Settings, Engine],
) -> None:
    _, engine = postgres
    result = runner.invoke(app, ["bootstrap", "--name", "CLI synthetic"])
    assert result.exit_code == 0
    token = result.stdout.strip().splitlines()[-1]
    assert WorkspaceService(Database(engine)).authenticate(token).role == "owner"
    assert runner.invoke(app, ["bootstrap", "--name", "CLI synthetic"]).exit_code == 1
    assert runner.invoke(app, ["bootstrap", "--name", "   "]).exit_code == 1


@pytest.mark.integration
def test_cli_readiness_has_failure_exit_code_without_worker(
    postgres: tuple[Settings, Engine],
) -> None:
    result = runner.invoke(app, ["ready"])
    assert result.exit_code == 1
    assert '"worker": false' in result.stdout
