from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Connection

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase


@pytest.fixture(autouse=True)
def isolated_mysql_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not inherit real local or CI credentials."""
    import os

    for key in os.environ:
        if key.startswith("MYSQL_") or key == "APP_ENV":
            monkeypatch.delenv(key)


@pytest.fixture
def metadata_rows() -> dict[str, list[dict[str, Any]]]:
    """Synthetic technical records, deliberately unrelated to any ERP schema."""
    columns = []
    for table in ("parent_records", "child_records"):
        for position, column in enumerate(("record_id", "revision", "label"), start=1):
            is_label = column == "label"
            columns.append(
                {
                    "table_name": table,
                    "name": column,
                    "ordinal_position": position,
                    "data_type": "varchar" if is_label else "int",
                    "column_type": "varchar(80)" if is_label else "int unsigned",
                    "is_nullable": "YES" if is_label else "NO",
                    "default_value": "" if is_label else None,
                    "comment": "Unicode label: नमूना" if is_label else "",
                    "extra": "",
                    "generation_expression": "",
                    "character_set": "utf8mb4" if is_label else None,
                    "collation": "utf8mb4_0900_ai_ci" if is_label else None,
                    "character_maximum_length": 80 if is_label else None,
                    "numeric_precision": None if is_label else 10,
                    "numeric_scale": None if is_label else 0,
                    "datetime_precision": None,
                }
            )
    keys = []
    indexes = []
    constraints = []
    for table in ("parent_records", "child_records"):
        constraints.append(
            {
                "table_name": table,
                "name": "PRIMARY",
                "constraint_type": "PRIMARY KEY",
                "enforced": "YES",
            }
        )
        for position, column in enumerate(("record_id", "revision"), start=1):
            keys.append(
                {
                    "table_name": table,
                    "name": "PRIMARY",
                    "column_name": column,
                    "ordinal_position": position,
                    "referenced_unique_position": None,
                    "referenced_schema": None,
                    "referenced_table": None,
                    "referenced_column": None,
                }
            )
            indexes.append(
                {
                    "table_name": table,
                    "name": "PRIMARY",
                    "non_unique": 0,
                    "index_type": "BTREE",
                    "ordinal_position": position,
                    "column_name": column,
                    "prefix_length": None,
                    "collation": "A",
                    "comment": "",
                    "index_comment": "",
                    "expression": None,
                    "visibility": "YES",
                }
            )
    for position, column in enumerate(("record_id", "revision"), start=1):
        keys.append(
            {
                "table_name": "child_records",
                "name": "fk_parent",
                "column_name": column,
                "ordinal_position": position,
                "referenced_unique_position": position,
                "referenced_schema": "fixture",
                "referenced_table": "parent_records",
                "referenced_column": column,
            }
        )
    constraints.extend(
        [
            {
                "table_name": "child_records",
                "name": "fk_parent",
                "constraint_type": "FOREIGN KEY",
                "enforced": "YES",
            },
            {
                "table_name": "child_records",
                "name": "chk_revision",
                "constraint_type": "CHECK",
                "enforced": "YES",
            },
            {
                "table_name": "parent_records",
                "name": "uq_label",
                "constraint_type": "UNIQUE",
                "enforced": "YES",
            },
        ]
    )
    keys.append(
        {
            "table_name": "parent_records",
            "name": "uq_label",
            "column_name": "label",
            "ordinal_position": 1,
            "referenced_unique_position": None,
            "referenced_schema": None,
            "referenced_table": None,
            "referenced_column": None,
        }
    )
    indexes.append(
        {
            "table_name": "parent_records",
            "name": "uq_label",
            "non_unique": 0,
            "index_type": "BTREE",
            "ordinal_position": 1,
            "column_name": "label",
            "prefix_length": 20,
            "collation": "D",
            "comment": "",
            "index_comment": "prefix index",
            "expression": None,
            "visibility": "NO",
        }
    )
    return {
        "SCHEMATA": [
            {
                "schema_name": "fixture",
                "default_character_set": "utf8mb4",
                "default_collation": "utf8mb4_0900_ai_ci",
            }
        ],
        "CAPABILITIES": [
            {"table_name": "STATISTICS", "column_name": "EXPRESSION"},
            {"table_name": "STATISTICS", "column_name": "IS_VISIBLE"},
            {"table_name": "TABLE_CONSTRAINTS", "column_name": "ENFORCED"},
            {"table_name": "CHECK_CONSTRAINTS", "column_name": "CHECK_CLAUSE"},
        ],
        "TABLES": [
            {
                "name": name,
                "table_type": "BASE TABLE",
                "engine": "InnoDB",
                "collation": "utf8mb4_0900_ai_ci",
                "comment": "Synthetic unit-test fixture",
            }
            for name in ("parent_records", "child_records")
        ],
        "COLUMNS": columns,
        "KEY_COLUMN_USAGE": keys,
        "REFERENTIAL_CONSTRAINTS": [
            {
                "table_name": "child_records",
                "name": "fk_parent",
                "referenced_unique_constraint": "PRIMARY",
                "update_rule": "RESTRICT",
                "delete_rule": "RESTRICT",
                "match_option": "NONE",
            }
        ],
        "STATISTICS": indexes,
        "TABLE_CONSTRAINTS": constraints,
        "CHECK_CONSTRAINTS": [{"name": "chk_revision", "check_clause": "(`revision` > 0)"}],
    }


@pytest.fixture
def fake_database(metadata_rows: dict[str, list[dict[str, Any]]]) -> MySQLDatabase:
    database = MagicMock(spec=MySQLDatabase)
    database.settings = Settings(_env_file=None, mysql_database="fixture")
    connection = MagicMock(spec=Connection)

    def execute(statement: Any, parameters: dict[str, Any] | None = None) -> MagicMock:
        sql = " ".join(str(statement).split())
        result = MagicMock()
        if sql == "SELECT VERSION()":
            result.scalar_one.return_value = "8.0.fixture"
            return result
        assert sql.startswith("SELECT ")
        assert parameters == {"schema": database.settings.mysql_database}
        source = sql.split("FROM INFORMATION_SCHEMA.")[1].split()[0]
        if source == "COLUMNS" and "TABLE_SCHEMA = 'information_schema'" in sql:
            source = "CAPABILITIES"
        result.mappings.return_value = metadata_rows[source]
        return result

    connection.execute.side_effect = execute

    @contextmanager
    def read_only_connection() -> Iterator[Connection]:
        yield connection

    database.read_only_connection.side_effect = read_only_connection
    database.fixture_connection = connection
    return database  # type: ignore[no-any-return]
