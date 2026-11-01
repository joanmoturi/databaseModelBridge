from alembic import context

from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import create_metadata_engine
from modelbridge.infrastructure.models import Base


def run_migrations() -> None:
    engine = create_metadata_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
