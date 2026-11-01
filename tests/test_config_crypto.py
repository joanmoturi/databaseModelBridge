import logging

import pytest
from cryptography.fernet import Fernet
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.crypto import CredentialCipher
from modelbridge.infrastructure.logging import SafeJsonFormatter


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://tester:synthetic-test-password@127.0.0.1/test_db",
        "encryption_keys": Fernet.generate_key().decode(),
        "environment": "test",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///metadata.db",
        "postgresql://tester:synthetic-test-password@localhost/db",
        "postgresql+psycopg://tester:short@localhost/db",
        "postgresql+psycopg://tester:synthetic-test-password@/db",
        "not-a-database-url",
    ],
)
def test_database_configuration_fails_closed(url: str) -> None:
    with pytest.raises(ValidationError):
        settings(database_url=url)


@pytest.mark.parametrize("key", ["", "bad", "AAAA", ",", "a" * 44])
def test_encryption_keys_must_be_valid(key: str) -> None:
    with pytest.raises(ValidationError):
        settings(encryption_keys=key)


@pytest.mark.parametrize(
    "origin", ["*", "https://example.org/path", "https://user:pass@example.org", "ftp://host"]
)
def test_cors_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        settings(cors_origins=[origin])


def test_production_requires_metadata_tls() -> None:
    with pytest.raises(ValidationError, match="verify-full"):
        settings(environment="production", cors_origins=["https://bridge.example.org"])


def test_production_rejects_http_origins() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://tester:synthetic-test-password@db.example.org/db"
                "?sslmode=verify-full"
            ),
        )


def test_configuration_does_not_expose_secrets() -> None:
    config = settings()
    assert "synthetic-test-password" not in repr(config)
    secret = "sensitive-invalid-connection"
    with pytest.raises(ValidationError) as error:
        settings(database_url=secret)
    assert secret not in str(error.value)


@given(st.text())
def test_cipher_roundtrip_and_no_plaintext_envelope(value: str) -> None:
    cipher = CredentialCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt(value)
    assert cipher.decrypt(encrypted) == value


def test_rotation_retains_old_values_and_uses_new_key() -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_cipher = CredentialCipher(old_key)
    envelope = old_cipher.encrypt("synthetic database credential")
    rotated = CredentialCipher(f"{new_key},{old_key}").rotate(envelope)
    assert CredentialCipher(new_key).decrypt(rotated) == "synthetic database credential"
    assert CredentialCipher(f"{new_key},{old_key}").decrypt(envelope) == (
        "synthetic database credential"
    )
    with pytest.raises(ValueError):
        old_cipher.decrypt(rotated)


def test_tampered_envelope_and_unknown_key_fail() -> None:
    cipher = CredentialCipher(Fernet.generate_key().decode())
    envelope = cipher.encrypt("synthetic")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        cipher.decrypt(envelope[:-4] + "xxxx")
    with pytest.raises(ValueError):
        CredentialCipher(Fernet.generate_key().decode()).decrypt(envelope)


def test_structured_formatter_excludes_exception_inputs() -> None:
    record = logging.LogRecord("modelbridge", logging.ERROR, "", 1, "database.failed", (), None)
    record.exc_text = "sensitive raw connection"
    output = SafeJsonFormatter().format(record)
    assert "database.failed" in output
    assert "sensitive raw connection" not in output
