from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.models import WorkerHeartbeat


def create_metadata_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def migration_config() -> Config:
    directory = Path(__file__).resolve().parents[1] / "migrations"
    config = Config()
    config.set_main_option("script_location", str(directory))
    return config


def schema_current(engine: Engine) -> bool:
    head = ScriptDirectory.from_config(migration_config()).get_current_head()
    with engine.connect() as connection:
        revisions = (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        )
    return revisions == [head]


def require_schema(engine: Engine) -> None:
    if not schema_current(engine):
        raise RuntimeError("Metadata schema is not current; run modelbridge db upgrade")


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self._sessions.begin() as session:
            yield session

    def readiness(self, worker_id: str, stale_seconds: int) -> dict[str, bool]:
        database_ok = schema_current(self.engine)
        with self.transaction() as session:
            heartbeat = session.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
            )
            worker_ok = (
                heartbeat is not None
                and heartbeat.state == "idle"
                and (datetime.now(UTC) - heartbeat.seen_at).total_seconds() <= stale_seconds
            )
        return {"metadata_database": database_ok, "worker": worker_ok}
