"""Connectivity and account-safety check without extracting or displaying ERP data."""

import logging

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase
from app.observability.logging import configure_logging


def main() -> int:
    configure_logging("INFO")
    logger = logging.getLogger("app.scripts.check_db")
    database = None
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        database = MySQLDatabase(settings)
        database.check_connectivity()
        logger.info("database_connectivity_and_read_only_grants_verified")
        return 0
    except Exception as exc:
        logger.error(
            "database_check_failed_check_configuration_and_grants",
            extra={
                "error_type": type(exc).__name__,
            },
        )
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
