import os

import pytest

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase
from app.sql_agent.schema.catalog import canonical_json
from app.sql_agent.schema.extractor import SchemaExtractor

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MYSQL_INTEGRATION") != "1",
        reason="Requires opt-in and a DBA-provisioned test schema",
    ),
]


def test_real_mysql_read_only_extraction() -> None:
    """No setup DDL: a DBA must provision tests/fixtures/mysql_schema.sql in a test DB."""
    database = MySQLDatabase(Settings())
    try:
        database.check_connectivity()
        first = SchemaExtractor(database).extract()
        second = SchemaExtractor(database).extract()
        assert canonical_json(first) == canonical_json(second)
        tables = {table.name: table for table in first.tables}
        assert {"fixture_parent", "fixture_child"} <= tables.keys()
        assert tables["fixture_child"].foreign_keys[0].columns == ["record_id", "revision"]
        assert tables["fixture_parent"].primary_key is not None
        assert tables["fixture_parent"].primary_key.columns == ["record_id", "revision"]
    finally:
        database.dispose()
