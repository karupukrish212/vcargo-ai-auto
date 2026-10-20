import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.db.mysql import MySQLDatabase
from app.sql_agent.schema.catalog import canonical_json, load_catalog, write_catalog
from app.sql_agent.schema.extractor import SchemaExtractionError, SchemaExtractor
from app.sql_agent.schema.models import RawSchemaCatalog


def test_extraction_preserves_composite_keys_and_technical_facts(
    fake_database: MySQLDatabase,
) -> None:
    catalog = SchemaExtractor(fake_database).extract()
    child, parent = catalog.tables
    assert child.name == "child_records"
    assert child.primary_key is not None
    assert child.primary_key.columns == ["record_id", "revision"]
    fk = child.foreign_keys[0]
    assert fk.columns == fk.referenced_columns == ["record_id", "revision"]
    assert fk.referenced_unique_positions == [1, 2]
    assert fk.referenced_table == "parent_records"
    assert parent.unique_keys[0].columns == ["label"]
    assert parent.columns[2].default == ""
    assert parent.columns[0].default is None
    assert parent.columns[2].nullable
    assert parent.columns[0].column_type == "int unsigned"
    assert "नमूना" in parent.columns[2].comment
    index = next(index for index in parent.indexes if index.name == "uq_label")
    assert index.parts[0].prefix_length == 20
    assert index.parts[0].collation == "D"
    assert index.visible is False
    check = next(item for item in child.constraints if item.constraint_type == "CHECK")
    assert check.check_clause == "(`revision` > 0)"
    assert check.enforced is True


def test_deterministic_serialization_and_loading(
    fake_database: MySQLDatabase, tmp_path: Path
) -> None:
    catalog = SchemaExtractor(fake_database).extract()
    path = tmp_path / "nested" / "schema_raw.json"
    assert write_catalog(catalog, path)
    original = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    assert not write_catalog(catalog, path)
    assert path.stat().st_mtime_ns == mtime
    assert load_catalog(path) == catalog
    assert original.decode() == canonical_json(catalog)
    assert original.endswith(b"\n")
    assert "extracted_at" not in json.loads(original)


def test_unordered_database_rows_produce_identical_json(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    first = canonical_json(SchemaExtractor(fake_database).extract())
    for rows in metadata_rows.values():
        rows.reverse()
    assert canonical_json(SchemaExtractor(fake_database).extract()) == first


@pytest.mark.parametrize(
    "source",
    [
        "SCHEMATA",
        "TABLES",
        "COLUMNS",
        "REFERENTIAL_CONSTRAINTS",
        "STATISTICS",
        "KEY_COLUMN_USAGE",
        "TABLE_CONSTRAINTS",
    ],
)
def test_missing_metadata_fails_closed(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
    source: str,
) -> None:
    metadata_rows[source].clear()
    with pytest.raises((SchemaExtractionError, ValidationError)):
        SchemaExtractor(fake_database).extract()


def test_missing_local_column_rejected(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["KEY_COLUMN_USAGE"][0]["column_name"] = "imagined_column"
    with pytest.raises(ValidationError):
        SchemaExtractor(fake_database).extract()


def test_missing_fk_target_rejected(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    for key in metadata_rows["KEY_COLUMN_USAGE"]:
        if key["name"] == "fk_parent":
            key["referenced_table"] = "missing_table"
    with pytest.raises(ValidationError):
        SchemaExtractor(fake_database).extract()


def test_cross_schema_fk_is_preserved_without_querying_the_other_schema(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    for key in metadata_rows["KEY_COLUMN_USAGE"]:
        if key["name"] == "fk_parent":
            key["referenced_schema"] = "external_schema"
    catalog = SchemaExtractor(fake_database).extract()
    assert catalog.tables[0].foreign_keys[0].referenced_schema == "external_schema"


def test_schema_name_is_bound_not_interpolated(fake_database: Any) -> None:
    fake_database.settings = fake_database.settings.model_copy(
        update={
            "mysql_database": "fixture'; DROP TABLE records; --",
        }
    )
    SchemaExtractor(fake_database).extract()
    for call in fake_database.fixture_connection.execute.call_args_list:
        assert "DROP TABLE" not in str(call.args[0])


def test_capabilities_are_detected_without_guessing(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["CAPABILITIES"].clear()
    metadata_rows["CHECK_CONSTRAINTS"].clear()
    for index in metadata_rows["STATISTICS"]:
        index["visibility"] = None
    for constraint in metadata_rows["TABLE_CONSTRAINTS"]:
        constraint["enforced"] = None
    catalog = SchemaExtractor(fake_database).extract()
    assert not catalog.capabilities.check_constraints
    assert all(index.visible is None for table in catalog.tables for index in table.indexes)
    sql_calls = [
        str(call.args[0]) for call in fake_database.fixture_connection.execute.call_args_list
    ]  # type: ignore[attr-defined]
    assert not any("FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS" in sql for sql in sql_calls)


def test_functional_index_expressions(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["STATISTICS"].append(
        {
            "table_name": "parent_records",
            "name": "idx_lower_label",
            "non_unique": 1,
            "index_type": "BTREE",
            "ordinal_position": 1,
            "column_name": None,
            "prefix_length": None,
            "collation": "A",
            "comment": "",
            "index_comment": "",
            "expression": "lower(`label`)",
            "visibility": "YES",
        }
    )
    catalog = SchemaExtractor(fake_database).extract()
    index = next(index for index in catalog.tables[1].indexes if index.name == "idx_lower_label")
    assert index.parts[0].expression == "lower(`label`)"


def test_malformed_catalog_and_extra_fields_are_rejected(fake_database: MySQLDatabase) -> None:
    data = SchemaExtractor(fake_database).extract().model_dump()
    data["inferred_business_meaning"] = "should not appear in a raw catalog"
    with pytest.raises(ValidationError):
        RawSchemaCatalog.model_validate(data)


def test_failed_atomic_replace_preserves_existing_catalog(
    fake_database: MySQLDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "schema_raw.json"
    path.write_text("existing catalog", encoding="utf-8")
    catalog = SchemaExtractor(fake_database).extract()
    monkeypatch.setattr(Path, "replace", MagicMock(side_effect=PermissionError("locked")))
    with pytest.raises(PermissionError):
        write_catalog(catalog, path)
    assert path.read_text() == "existing catalog"
    assert not list(tmp_path.glob("*.tmp"))


def test_schema_change_during_extraction_rejected(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["COLUMNS"][0]["table_name"] = "unexpected_new_table"
    with pytest.raises(SchemaExtractionError):
        SchemaExtractor(fake_database).extract()


def test_orphan_key_metadata_rejected(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["TABLE_CONSTRAINTS"] = [
        row for row in metadata_rows["TABLE_CONSTRAINTS"] if row["name"] != "fk_parent"
    ]
    with pytest.raises(SchemaExtractionError):
        SchemaExtractor(fake_database).extract()


def test_unknown_nullability_rejected(
    fake_database: MySQLDatabase,
    metadata_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata_rows["COLUMNS"][0]["is_nullable"] = "UNKNOWN"
    with pytest.raises(SchemaExtractionError):
        SchemaExtractor(fake_database).extract()


def test_complete_example_matches_synthetic_extraction(fake_database: MySQLDatabase) -> None:
    example = Path(__file__).parents[1] / "fixtures" / "schema_raw.example.json"
    assert canonical_json(load_catalog(example)) == canonical_json(
        SchemaExtractor(fake_database).extract()
    )
