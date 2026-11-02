import hashlib
import time

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert

from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import ApiRateBucket


class RateExceeded(Exception):
    pass


def consume_rate(database: Database, identity: str, limit: int) -> None:
    window = int(time.time()) // 60
    bucket = hashlib.sha256(identity.encode()).hexdigest()
    statement = insert(ApiRateBucket).values(id=bucket, window=window, count=1)
    returning_statement = statement.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "window": window,
            "count": case((ApiRateBucket.window == window, ApiRateBucket.count + 1), else_=1),
        },
    ).returning(ApiRateBucket.count)
    with database.transaction() as session:
        count = session.execute(returning_statement).scalar_one()
    if count > limit:
        raise RateExceeded
