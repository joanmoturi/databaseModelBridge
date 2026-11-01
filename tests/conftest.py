import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import create_metadata_engine, migration_config


@pytest.fixture
def postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Settings, Engine]]:
    raw_url = os.environ.get("MODELBRIDGE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Disposable PostgreSQL URL not configured")
    if os.environ.get("MODELBRIDGE_TEST_ALLOW_DATABASE_WRITES") != "true":
        pytest.fail("Explicit disposable database write acknowledgement required")
    url = make_url(raw_url)
    if (
        url.host not in {"127.0.0.1", "localhost"}
        or not url.database
        or not url.database.startswith("modelbridge_test_")
    ):
        pytest.fail("Integration tests require a loopback modelbridge_test_* database")
    schema = "mb_test_" + uuid4().hex
    admin = create_engine(raw_url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    test_url_text = test_url.render_as_string(hide_password=False)
    monkeypatch.setenv("MODELBRIDGE_DATABASE_URL", test_url_text)
    monkeypatch.setenv("MODELBRIDGE_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("MODELBRIDGE_ENVIRONMENT", "test")
    settings = Settings(_env_file=None)
    engine = create_metadata_engine(settings)
    try:
        command.upgrade(migration_config(), "head")
        yield settings, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
