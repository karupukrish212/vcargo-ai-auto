"""Run from the repository root after installing the project: python scripts/schema_extractor.py."""

import argparse
import logging
from pathlib import Path
from time import perf_counter

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase
from app.observability.logging import configure_logging
from app.sql_agent.schema.catalog import write_catalog
from app.sql_agent.schema.extractor import SchemaExtractor

logger = logging.getLogger("app.scripts.schema_extractor")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract verified technical MySQL metadata read-only"
    )
    parser.add_argument("--output", type=Path, help="Override SCHEMA_OUTPUT_PATH")
    args = parser.parse_args()
    configure_logging("INFO")
    database = None
    started = perf_counter()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        database = MySQLDatabase(settings)
        catalog = SchemaExtractor(database).extract()
        changed = write_catalog(catalog, args.output or settings.schema_output_path)
        logger.info(
            "schema_catalog_written" if changed else "schema_catalog_unchanged",
            extra={
                "table_count": len(catalog.tables),
                "duration_ms": round((perf_counter() - started) * 1000),
            },
        )
        return 0
    except Exception as exc:
        logger.error(
            "schema_extraction_failed_check_configuration_and_grants",
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
