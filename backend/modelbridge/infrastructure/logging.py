import json
import logging
from datetime import UTC, datetime


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only fixed event messages are emitted; arbitrary exceptions/inputs are excluded.
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                "logger": record.name,
            }
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logger = logging.getLogger("modelbridge")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
