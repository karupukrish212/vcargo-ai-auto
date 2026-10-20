"""Opt-in, read-only validation against an existing catalog and database.

Unlike the synthetic fixture integration test, this test never requires DDL or
customer records. Its only database reads are technical metadata and SELECT 1.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase
from app.main import create_app
from app.sql_agent.schema.catalog import canonical_json, load_catalog
from app.sql_agent.schema.extractor import SchemaExtractor

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MYSQL_CATALOG_INTEGRATION") != "1",
        reason="Requires opt-in, a configured reader, and an existing extracted catalog",
    ),
]


def test_live_catalog_completeness_idempotence_and_readiness() -> None:
    settings = Settings()
    original = settings.schema_output_path.read_bytes()
    catalog = load_catalog(settings.schema_output_path)
    database = MySQLDatabase(settings)
    try:
        database.check_connectivity()
        extracted = SchemaExtractor(database).extract()
        assert canonical_json(extracted).encode("utf-8") == original
        assert canonical_json(catalog) == canonical_json(extracted)

        queries = {
            "tables": "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=:schema",
            "columns": "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=:schema",
            "index_parts": (
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA=:schema"
            ),
            "foreign_keys": (
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA=:schema"
            ),
            "constraints": (
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA=:schema"
            ),
        }
        expected = {
            "tables": len(catalog.tables),
            "columns": sum(len(table.columns) for table in catalog.tables),
            "index_parts": sum(
                len(index.parts) for table in catalog.tables for index in table.indexes
            ),
            "foreign_keys": sum(len(table.foreign_keys) for table in catalog.tables),
            "constraints": sum(len(table.constraints) for table in catalog.tables),
        }
        with database.read_only_connection() as connection:
            actual = {
                name: connection.execute(
                    text(sql), {"schema": settings.mysql_database}
                ).scalar_one()
                for name, sql in queries.items()
            }
        assert actual == expected

        with TestClient(create_app(settings, database)) as client:
            assert client.get("/health").json() == {"status": "ok"}
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
    finally:
        database.dispose()
