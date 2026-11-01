import logging
import signal
import threading
from datetime import UTC, datetime
from types import FrameType

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from modelbridge import __version__
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database, create_metadata_engine, require_schema
from modelbridge.infrastructure.logging import configure_logging
from modelbridge.infrastructure.models import WorkerHeartbeat

logger = logging.getLogger("modelbridge.worker")


def heartbeat(database: Database, worker_id: str, state: str = "idle") -> None:
    statement = insert(WorkerHeartbeat).values(
        worker_id=worker_id, version=__version__, state=state, seen_at=datetime.now(UTC)
    )
    statement = statement.on_conflict_do_update(
        index_elements=["worker_id"],
        set_={"version": __version__, "state": state, "seen_at": datetime.now(UTC)},
    )
    with database.transaction() as session:
        session.execute(statement)


def run() -> None:
    configure_logging()
    settings = Settings()
    engine = create_metadata_engine(settings)
    database = Database(engine)
    stop = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        require_schema(engine)
        logger.info("worker.started")
        while not stop.is_set():
            try:
                heartbeat(database, settings.worker_id)
            except SQLAlchemyError:
                logger.error("worker.heartbeat_failed")
            stop.wait(settings.heartbeat_seconds)
        try:
            heartbeat(database, settings.worker_id, "stopped")
        except SQLAlchemyError:
            logger.error("worker.shutdown_heartbeat_failed")
    finally:
        engine.dispose()
        logger.info("worker.stopped")


if __name__ == "__main__":
    run()
