from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from app.db.mysql import MySQLDatabase
from app.metadata.loader import BusinessCatalog, MetadataValidationError, read_yaml
from app.metadata.models import ColumnMetadata, TableMetadata, TableScope, VerifiedQueryExample
from app.sql_agent.schema.catalog import write_catalog
from app.sql_agent.schema.extractor import SchemaExtractor
from scripts import create_metadata_templates

REVIEW = {
    "verified": True,
    "source": "manual",
    "reviewed_by": "test_expert",
    "reviewed_on": "2026-10-09",
}


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def business_root(tmp_path: Path) -> Path:
    documents: dict[str, dict[str, Any]] = {
        "policies/table_scope.yaml": {},
        "columns/common_columns.yaml": {"profiles": {}},
        "glossary/business_terms.yaml": {"terms": {}},
        "metrics/metrics.yaml": {"metrics": {}},
        "relationships/logical_relationships.yaml": {"relationships": []},
        "examples/verified_queries.yaml": {"queries": []},
        "tables/parent_records.yaml": {
            "table_name": "parent_records",
            "business_name": "Test records",
            "description": "Synthetic test records",
            "domain": "test",
            **REVIEW,
            "columns": {
                "record_id": {
                    "business_name": "Identifier",
                    "description": "Synthetic identifier",
                    **REVIEW,
                }
            },
        },
    }
    for name, document in documents.items():
        save(tmp_path / name, document)
    return tmp_path


def test_verified_metadata_loading(business_root: Path, fake_database: MySQLDatabase) -> None:
    catalog = BusinessCatalog(business_root, SchemaExtractor(fake_database).extract())
    assert list(catalog.approved_tables()) == ["parent_records"]
    assert list(catalog.approved_columns("parent_records")) == ["record_id"]
    assert catalog.status()["draft_table_files"] == 0


def test_excluded_table_remains_in_raw_schema_but_never_approved(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    save(business_root / "policies/table_scope.yaml", {"excluded_patterns": ["parent*"]})
    raw = SchemaExtractor(fake_database).extract()
    catalog = BusinessCatalog(business_root, raw)
    assert any(table.name == "parent_records" for table in raw.tables)
    assert catalog.approved_tables() == {}
    assert catalog.approved_columns("parent_records") == {}


@pytest.mark.parametrize(
    "change",
    [
        {"table_name": "imagined_table"},
        {"columns": {"imagined_column": {}}},
        {"columns": {"record_id": {"profile": "imagined_profile"}}},
    ],
)
def test_unknown_metadata_references_rejected(
    business_root: Path,
    fake_database: MySQLDatabase,
    change: dict[str, Any],
) -> None:
    path = business_root / "tables/parent_records.yaml"
    data = yaml.safe_load(path.read_text())
    data.update(change)
    save(path, data)
    with pytest.raises(MetadataValidationError):
        BusinessCatalog(business_root, SchemaExtractor(fake_database).extract())


@pytest.mark.parametrize("source", ["llm_generated", "schema_generated"])
def test_generated_drafts_cannot_be_promoted_without_manual_review(source: str) -> None:
    with pytest.raises(ValidationError):
        ColumnMetadata.model_validate(
            {"business_name": "ID", "description": "ID", **REVIEW, "source": source}
        )


def test_review_audit_and_actual_boolean_required() -> None:
    with pytest.raises(ValidationError):
        TableMetadata(table_name="example", verified=True)
    with pytest.raises(ValidationError):
        TableMetadata.model_validate({"table_name": "example", "verified": "true"})


def test_unquoted_enum_keys_and_blank_meanings_rejected() -> None:
    with pytest.raises(ValidationError):
        ColumnMetadata.model_validate({"values": {0: "INACTIVE", 1: "ACTIVE"}})
    with pytest.raises(ValidationError):
        ColumnMetadata.model_validate(
            {"business_name": "Flag", "description": "Flag", "values": {"0": ""}, **REVIEW}
        )


def test_duplicate_yaml_keys_and_unsafe_tags_rejected(tmp_path: Path) -> None:
    path = tmp_path / "scope.yaml"
    path.write_text("excluded_tables: []\nexcluded_tables: [example]\n")
    with pytest.raises(MetadataValidationError):
        read_yaml(path, TableScope)
    path.write_text("!!python/object/apply:os.system ['echo must_not_run']\n")
    with pytest.raises(MetadataValidationError):
        read_yaml(path, TableScope)


def test_shared_profile_and_local_column_both_require_review(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    profile = {"business_name": "Identifier", "description": "Shared test identifier", **REVIEW}
    save(business_root / "columns/common_columns.yaml", {"profiles": {"id": profile}})
    path = business_root / "tables/parent_records.yaml"
    table = yaml.safe_load(path.read_text())
    table["columns"] = {"record_id": {"profile": "id"}}
    save(path, table)
    schema = SchemaExtractor(fake_database).extract()
    assert BusinessCatalog(business_root, schema).approved_columns("parent_records") == {}
    table["columns"]["record_id"].update(REVIEW)
    save(path, table)
    catalog = BusinessCatalog(business_root, schema)
    assert (
        catalog.approved_columns("parent_records")["record_id"].description
        == "Shared test identifier"
    )
    profile["verified"] = False
    save(business_root / "columns/common_columns.yaml", {"profiles": {"id": profile}})
    assert BusinessCatalog(business_root, schema).approved_columns("parent_records") == {}


def test_native_date_cannot_use_text_parsing_profile(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    profile = {"date": {"storage": "native", "timezone": "Asia/Kolkata"}}
    save(business_root / "columns/common_columns.yaml", {"profiles": {"date": profile}})
    table = {"table_name": "parent_records", "columns": {"label": {"profile": "date"}}}
    save(business_root / "tables/parent_records.yaml", table)
    with pytest.raises(MetadataValidationError):
        BusinessCatalog(business_root, SchemaExtractor(fake_database).extract())


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError):
        ColumnMetadata.model_validate({"date": {"storage": "native", "timezone": "Not/AZone"}})


def test_unknown_metric_and_relationship_columns_rejected(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    schema = SchemaExtractor(fake_database).extract()
    save(
        business_root / "metrics/metrics.yaml",
        {"metrics": {"m": {"required_columns": ["parent_records.missing"]}}},
    )
    with pytest.raises(MetadataValidationError):
        BusinessCatalog(business_root, schema)
    save(business_root / "metrics/metrics.yaml", {"metrics": {}})
    save(
        business_root / "relationships/logical_relationships.yaml",
        {
            "relationships": [
                {
                    "from": {"table": "parent_records", "column": "missing"},
                    "to": {"table": "child_records", "column": "record_id"},
                    "relationship": "unknown",
                }
            ]
        },
    )
    with pytest.raises(MetadataValidationError):
        BusinessCatalog(business_root, schema)


def test_template_generation_preserves_existing_expert_edits(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    schema = SchemaExtractor(fake_database).extract()
    path = business_root / "generated/schema_raw.json"
    write_catalog(schema, path)
    reviewed = business_root / "tables/parent_records.yaml"
    original = reviewed.read_bytes()
    arguments = [
        "create_metadata_templates.py",
        "--metadata",
        str(business_root),
        "--schema",
        str(path),
    ]
    with patch("sys.argv", arguments):
        assert create_metadata_templates.main() == 0
        assert create_metadata_templates.main() == 0
    assert reviewed.read_bytes() == original
    assert (business_root / "tables/child_records.yaml").is_file()


def test_verified_example_requires_declared_tables() -> None:
    with pytest.raises(ValidationError):
        VerifiedQueryExample.model_validate(
            {
                "question": "Synthetic report",
                "sql": "SELECT 1",
                "related_tables": [],
                **REVIEW,
            }
        )


def test_common_profiles_follow_actual_columns_and_remain_drafts(
    business_root: Path,
    fake_database: MySQLDatabase,
) -> None:
    schema = SchemaExtractor(fake_database).extract()
    child = next(table for table in schema.tables if table.name == "child_records")
    created_by = child.columns[0].model_copy(
        update={
            "name": "createdby",
            "ordinal_position": max(column.ordinal_position for column in child.columns) + 1,
        }
    )
    child_with_creator = child.model_copy(update={"columns": [*child.columns, created_by]})
    schema = schema.model_copy(
        update={
            "tables": [
                child_with_creator if table.name == child.name else table for table in schema.tables
            ]
        }
    )
    save(
        business_root / "columns/common_columns.yaml",
        {
            "profiles": {
                "created_by_user": {
                    "business_name": "Record Creator",
                    "description": "Draft creator reference",
                }
            }
        },
    )
    arguments = ["create_metadata_templates.py", "--metadata", str(business_root)]
    with (
        patch("sys.argv", arguments),
        patch.object(create_metadata_templates, "load_catalog", return_value=schema),
    ):
        assert create_metadata_templates.main() == 0
    generated = read_yaml(business_root / "tables/child_records.yaml", TableMetadata)
    assert generated.columns["createdby"].profile == "created_by_user"
    assert "description" not in generated.columns["createdby"].model_fields_set
    assert not generated.columns["createdby"].verified
    assert "userid" not in generated.columns
    catalog = BusinessCatalog(business_root, schema)
    assert catalog.resolve_column("child_records", "createdby").description == (
        "Draft creator reference"
    )
    assert catalog.approved_columns("child_records") == {}
