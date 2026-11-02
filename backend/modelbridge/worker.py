import logging
import signal
import threading
import time
from datetime import UTC, datetime
from types import FrameType

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from modelbridge import __version__
from modelbridge.application.connections import ConnectionService
from modelbridge.application.migration_engine import JobService, PlanService
from modelbridge.application.webhooks import WebhookService
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
        jobs = JobService(database, PlanService(database, ConnectionService(database, settings)))
        hooks = WebhookService(database, settings)
        active = threading.Event()

        def services() -> None:
            last_heartbeat = 0.0
            while not stop.is_set():
                try:
                    if time.monotonic() - last_heartbeat >= settings.heartbeat_seconds:
                        heartbeat(
                            database, settings.worker_id, "running" if active.is_set() else "idle"
                        )
                        last_heartbeat = time.monotonic()
                    hooks.deliver_one()
                except Exception:
                    logger.error("worker.auxiliary_failed")
                stop.wait(0.5)

        auxiliary = threading.Thread(target=services, daemon=True)
        auxiliary.start()
        logger.info("worker.started")
        while not stop.is_set():
            try:
                active.set()
                worked = jobs.process_one(stop.is_set)
            except SQLAlchemyError:
                logger.error("worker.job_metadata_failed")
                worked = False
            finally:
                active.clear()
            if not worked:
                stop.wait(0.25)
        auxiliary.join(timeout=15)
        try:
            heartbeat(database, settings.worker_id, "stopped")
        except SQLAlchemyError:
            logger.error("worker.shutdown_heartbeat_failed")
    finally:
        engine.dispose()
        logger.info("worker.stopped")


if __name__ == "__main__":
    run()
