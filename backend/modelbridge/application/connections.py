import hashlib
import ipaddress
import json
import socket
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError

from modelbridge.domain.access import AccessDenied, ResourceConflict
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.crypto import CredentialCipher
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import (
    AuditEvent,
    DatabaseConnection,
    SchemaSnapshot,
    Workspace,
)


class ConnectionUnavailable(Exception):
    pass


def allowed_address(host: str, port: int, networks: list[str]) -> str:
    allowed = [ipaddress.ip_network(network, strict=False) for network in networks]
    try:
        addresses = sorted(
            {str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
    except OSError:
        raise ConnectionUnavailable from None
    if not addresses:
        raise ConnectionUnavailable
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        if (
            parsed.is_unspecified
            or parsed.is_multicast
            or parsed.is_link_local
            or not any(parsed in network for network in allowed)
        ):
            raise ConnectionUnavailable
    return addresses[0]


class ConnectionService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.cipher = CredentialCipher(settings.encryption_keys.get_secret_value())

    def get(self, workspace_id: str, connection_id: str) -> DatabaseConnection:
        with self.database.transaction() as session:
            connection = session.scalar(
                select(DatabaseConnection).where(
                    DatabaseConnection.workspace_id == workspace_id,
                    DatabaseConnection.id == connection_id,
                    DatabaseConnection.deleted_at.is_(None),
                )
            )
            if connection is None:
                raise AccessDenied
            return connection

    def list_connections(
        self, workspace_id: str, offset: int, limit: int
    ) -> list[DatabaseConnection]:
        with self.database.transaction() as session:
            return list(
                session.scalars(
                    select(DatabaseConnection)
                    .where(
                        DatabaseConnection.workspace_id == workspace_id,
                        DatabaseConnection.deleted_at.is_(None),
                    )
                    .order_by(DatabaseConnection.created_at, DatabaseConnection.id)
                    .offset(offset)
                    .limit(limit)
                )
            )

    def save(
        self,
        workspace_id: str,
        name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str | None,
        sslmode: str,
        connection_id: str | None = None,
        actor_id: str | None = None,
    ) -> DatabaseConnection:
        if self.settings.environment == "production" and sslmode != "verify-full":
            raise ValueError("Production database connections require verify-full TLS")
        allowed_address(host, port, self.settings.allowed_database_networks)
        with self.database.transaction() as session:
            if session.get(Workspace, workspace_id, with_for_update=True) is None:
                raise AccessDenied
            if connection_id is None:
                count = session.scalar(
                    select(func.count())
                    .select_from(DatabaseConnection)
                    .where(
                        DatabaseConnection.workspace_id == workspace_id,
                        DatabaseConnection.deleted_at.is_(None),
                    )
                )
                if count is not None and count >= self.settings.max_connections_per_workspace:
                    raise ResourceConflict("Connection quota exceeded")
                if password is None:
                    raise ValueError("Password required for a new connection")
                connection = DatabaseConnection(
                    workspace_id=workspace_id,
                    name=name,
                    host=host,
                    port=port,
                    database=database,
                    username=username,
                    password_envelope=self.cipher.encrypt(password),
                    sslmode=sslmode,
                    created_at=datetime.now(UTC),
                )
                session.add(connection)
            else:
                found = session.scalar(
                    select(DatabaseConnection).where(
                        DatabaseConnection.id == connection_id,
                        DatabaseConnection.workspace_id == workspace_id,
                        DatabaseConnection.deleted_at.is_(None),
                    )
                )
                if found is None:
                    raise AccessDenied
                connection = found
                destination_changed = (
                    connection.host != host
                    or connection.port != port
                    or connection.database != database
                    or connection.username != username
                )
                if password is None and destination_changed:
                    raise ValueError("Replacement password required when destination changes")
                tls_strength = {"disable": 0, "require": 1, "verify-full": 2}
                if tls_strength[sslmode] < tls_strength[connection.sslmode]:
                    raise ValueError("TLS downgrades are not supported; create a new connection")
                connection.name, connection.host, connection.port = name, host, port
                connection.database, connection.username, connection.sslmode = (
                    database,
                    username,
                    sslmode,
                )
                if password is not None:
                    connection.password_envelope = self.cipher.encrypt(password)
            session.flush()
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    object_id=connection.id,
                    action="connection.create" if connection_id is None else "connection.update",
                    created_at=datetime.now(UTC),
                )
            )
        return connection

    def delete(self, workspace_id: str, connection_id: str, actor_id: str | None = None) -> None:
        with self.database.transaction() as session:
            connection = session.scalar(
                select(DatabaseConnection).where(
                    DatabaseConnection.id == connection_id,
                    DatabaseConnection.workspace_id == workspace_id,
                    DatabaseConnection.deleted_at.is_(None),
                )
            )
            if connection is None:
                raise AccessDenied
            connection.deleted_at = datetime.now(UTC)
            connection.password_envelope = ""
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    object_id=connection.id,
                    action="connection.delete",
                    created_at=datetime.now(UTC),
                )
            )

    def target_engine(self, connection: DatabaseConnection, *, writable: bool = False) -> Engine:
        address = allowed_address(
            connection.host, connection.port, self.settings.allowed_database_networks
        )
        if self.settings.environment == "production" and connection.sslmode != "verify-full":
            raise ConnectionUnavailable
        return create_engine(
            URL.create(
                "postgresql+psycopg",
                username=connection.username,
                password=self.cipher.decrypt(connection.password_envelope),
                host=connection.host,
                port=connection.port,
                database=connection.database,
            ),
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
            hide_parameters=True,
            connect_args={
                "hostaddr": address,
                "sslmode": connection.sslmode,
                "connect_timeout": self.settings.connection_timeout_seconds,
                "options": (
                    "-cstatement_timeout=10000 -clock_timeout=5000 "
                    f"-cdefault_transaction_read_only={'off' if writable else 'on'}"
                ),
                **({"sslrootcert": "system"} if connection.sslmode == "verify-full" else {}),
            },
        )

    def test(self, workspace_id: str, connection_id: str) -> int:
        connection = self.get(workspace_id, connection_id)
        engine = self.target_engine(connection)
        try:
            with engine.connect() as database:
                return int(database.execute(text("SHOW server_version_num")).scalar_one())
        except SQLAlchemyError:
            raise ConnectionUnavailable from None
        finally:
            engine.dispose()

    def discover(
        self, workspace_id: str, connection_id: str, schema: str, actor_id: str | None = None
    ) -> SchemaSnapshot:
        connection = self.get(workspace_id, connection_id)
        engine = self.target_engine(connection)
        try:
            inspector = cast(PGInspector, inspect(engine))
            if schema not in inspector.get_schema_names():
                raise ValueError("Schema does not exist or is not visible")
            tables = sorted(inspector.get_table_names(schema=schema))
            if len(tables) > 200:
                raise ValueError("Schema exceeds supported table discovery limit")
            objects: dict[str, object] = {"tables": {}, "enums": inspector.get_enums(schema=schema)}
            discovered: dict[str, object] = {}
            for table in tables:
                columns = inspector.get_columns(table, schema=schema)
                if len(columns) > 500:
                    raise ValueError("Table exceeds supported column discovery limit")
                discovered[table] = {
                    "columns": [
                        {
                            "name": column["name"],
                            "type": str(column["type"]),
                            "nullable": column["nullable"],
                            "default": column.get("default"),
                            "identity": column.get("identity"),
                            "computed": column.get("computed"),
                        }
                        for column in columns
                    ],
                    "primary_key": inspector.get_pk_constraint(table, schema=schema),
                    "foreign_keys": inspector.get_foreign_keys(table, schema=schema),
                    "unique": inspector.get_unique_constraints(table, schema=schema),
                    "indexes": inspector.get_indexes(table, schema=schema),
                    "checks": inspector.get_check_constraints(table, schema=schema),
                }
            objects["tables"] = discovered
            objects = cast(dict[str, object], json.loads(json.dumps(objects, default=str)))
            fingerprint = hashlib.sha256(
                json.dumps(objects, sort_keys=True, default=str).encode()
            ).hexdigest()
            with self.database.transaction() as session:
                snapshot = SchemaSnapshot(
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    schema_name=schema,
                    fingerprint=fingerprint,
                    objects=objects,
                    created_at=datetime.now(UTC),
                )
                session.add(snapshot)
                session.flush()
                session.add(
                    AuditEvent(
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        object_id=snapshot.id,
                        action="schema.discover",
                        created_at=datetime.now(UTC),
                    )
                )
            return snapshot
        except SQLAlchemyError:
            raise ConnectionUnavailable from None
        finally:
            engine.dispose()

    def snapshots(self, workspace_id: str, offset: int, limit: int) -> list[SchemaSnapshot]:
        with self.database.transaction() as session:
            return list(
                session.scalars(
                    select(SchemaSnapshot)
                    .where(
                        SchemaSnapshot.workspace_id == workspace_id,
                    )
                    .order_by(SchemaSnapshot.created_at.desc(), SchemaSnapshot.id)
                    .offset(offset)
                    .limit(limit)
                )
            )

    def snapshot(self, workspace_id: str, snapshot_id: str) -> SchemaSnapshot:
        with self.database.transaction() as session:
            snapshot = session.scalar(
                select(SchemaSnapshot).where(
                    SchemaSnapshot.id == snapshot_id,
                    SchemaSnapshot.workspace_id == workspace_id,
                )
            )
            if snapshot is None:
                raise AccessDenied
            return snapshot
