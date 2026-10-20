import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import URL, Connection, Engine, create_engine, text

from app.config.settings import Settings


class DatabaseSafetyError(RuntimeError):
    """The configured database account or connection is unsafe."""


def validate_read_only_grants(
    grants: list[str],
    database: str,
    *,
    partial_revokes: bool = False,
) -> None:
    """Fail closed on roles, global SELECT, writes, grant options, or other databases.

    Require schema-wide SELECT so extraction is not silently limited by table grants.
    Role-based grants must be flattened by a DBA into an explicit dedicated account.
    """
    identifier = database.replace("`", "``")
    if not partial_revokes:
        identifier = identifier.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    target = f"`{identifier}`.*"
    has_schema_select = False
    for grant in grants:
        match = re.fullmatch(r"GRANT (.+?) ON (.+?) TO .+", grant, re.IGNORECASE)
        if not match or re.search(r"\bWITH GRANT OPTION\b", grant, re.IGNORECASE):
            raise DatabaseSafetyError("Unsupported grants or role-based account")
        privileges = {part.strip().upper() for part in match[1].split(",")}
        if not privileges <= {"USAGE", "SELECT", "SHOW VIEW"}:
            raise DatabaseSafetyError("Account has privileges beyond the read-only allowlist")
        scope = match[2]
        if scope == "*.*" and privileges == {"USAGE"}:
            continue
        if scope != target:
            raise DatabaseSafetyError("Account must be restricted to the configured database")
        has_schema_select |= "SELECT" in privileges
    if not has_schema_select:
        raise DatabaseSafetyError("Schema-wide SELECT is required for complete extraction")


class MySQLDatabase:
    """Own a lazily connecting pool; expose only verified read-only transactions."""

    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        if not settings.database_configured:
            raise DatabaseSafetyError("MySQL configuration is incomplete")
        self.settings = settings
        self.engine = engine if engine is not None else self._create_engine()

    def _create_engine(self) -> Engine:
        settings = self.settings
        url = URL.create(
            "mysql+pymysql",
            username=settings.mysql_user,
            password=settings.mysql_password.get_secret_value(),
            host=settings.mysql_host,
            port=settings.mysql_port,
            database=settings.mysql_database,
            query={"charset": "utf8mb4"},
        )
        connect_args: dict[str, Any] = {
            "connect_timeout": settings.mysql_connect_timeout_seconds,
            "read_timeout": settings.mysql_read_timeout_seconds,
            "write_timeout": settings.mysql_read_timeout_seconds,
        }
        if settings.mysql_ssl_ca:
            connect_args.update(
                ssl_ca=str(settings.mysql_ssl_ca), ssl_verify_cert=True, ssl_verify_identity=True
            )
            if settings.mysql_ssl_cert:
                connect_args.update(
                    ssl_cert=str(settings.mysql_ssl_cert), ssl_key=str(settings.mysql_ssl_key)
                )
        return create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_size=settings.mysql_pool_size,
            max_overflow=0,
            pool_timeout=settings.mysql_connect_timeout_seconds,
            pool_recycle=settings.mysql_pool_recycle_seconds,
            echo=False,
            hide_parameters=True,
        )

    @contextmanager
    def read_only_connection(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            try:
                connection.exec_driver_sql(
                    f"SET SESSION MAX_EXECUTION_TIME = {self.settings.mysql_execution_timeout_ms}"
                )
                connection.exec_driver_sql("START TRANSACTION READ ONLY")
                principal = str(connection.execute(text("SELECT CURRENT_USER()")).scalar_one())
                if principal.rsplit("@", 1)[0] != self.settings.mysql_user:
                    raise DatabaseSafetyError(
                        "Authenticated principal differs from configured reader"
                    )
                variables = list(
                    connection.exec_driver_sql("SHOW GLOBAL VARIABLES LIKE 'partial_revokes'")
                )
                partial_revokes = bool(variables and str(variables[0][1]).upper() == "ON")
                grants = [str(row[0]) for row in connection.exec_driver_sql("SHOW GRANTS")]
                validate_read_only_grants(
                    grants,
                    self.settings.mysql_database,
                    partial_revokes=partial_revokes,
                )
                yield connection
            finally:
                connection.rollback()

    def check_connectivity(self) -> None:
        with self.read_only_connection() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                raise DatabaseSafetyError("Database connectivity check failed")

    def dispose(self) -> None:
        self.engine.dispose()
