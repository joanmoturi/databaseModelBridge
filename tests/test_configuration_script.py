import hashlib
from pathlib import Path

import pytest

from modelbridge.infrastructure.config import Settings
from scripts.configure import write_configuration


def test_configuration_generates_valid_secrets_without_printing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / ".env"
    write_configuration(destination)
    configuration = Settings(_env_file=destination)
    captured = capsys.readouterr().out
    assert configuration.encryption_keys.get_secret_value() not in captured
    assert configuration.database_url.get_secret_value() not in captured


def test_configuration_refuses_to_overwrite_existing_secrets(tmp_path: Path) -> None:
    destination = tmp_path / ".env"
    write_configuration(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        write_configuration(destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
