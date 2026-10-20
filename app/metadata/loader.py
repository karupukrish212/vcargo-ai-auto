from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.metadata.models import (
    ColumnMetadata,
    ColumnProfiles,
    ExamplesCatalog,
    GlossaryCatalog,
    GlossaryEntry,
    LogicalRelationship,
    MetricMetadata,
    MetricsCatalog,
    RelationshipsCatalog,
    TableMetadata,
    TableScope,
    VerifiedQueryExample,
)
from app.sql_agent.schema.models import RawSchemaCatalog


class MetadataValidationError(ValueError):
    """Business knowledge is malformed or contradicts the extracted schema."""


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML's default loader silently overwrites duplicate keys; business facts must not."""


def _unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, float, bool)):
            raise MetadataValidationError("YAML mapping keys must be scalar")
        if key in result:
            raise MetadataValidationError("Duplicate YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_yaml[Model: BaseModel](path: Path, model: type[Model]) -> Model:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise MetadataValidationError(f"Invalid YAML in {path.name}") from exc
    if not isinstance(data, dict):
        raise MetadataValidationError(f"Expected a YAML object in {path.name}")
    return model.model_validate(data)


class BusinessCatalog:
    """Load drafts for review; expose only explicitly reviewed, in-scope knowledge."""

    def __init__(self, root: Path, schema: RawSchemaCatalog) -> None:
        self.schema = schema
        self.scope = read_yaml(root / "policies/table_scope.yaml", TableScope)
        self.profiles = read_yaml(root / "columns/common_columns.yaml", ColumnProfiles).profiles
        self.terms = read_yaml(root / "glossary/business_terms.yaml", GlossaryCatalog).terms
        self.metrics = read_yaml(root / "metrics/metrics.yaml", MetricsCatalog).metrics
        self.relationships = read_yaml(
            root / "relationships/logical_relationships.yaml",
            RelationshipsCatalog,
        ).relationships
        self.examples = read_yaml(root / "examples/verified_queries.yaml", ExamplesCatalog).queries
        self.tables: dict[str, TableMetadata] = {}
        for path in sorted((root / "tables").glob("*.yaml")):
            table = read_yaml(path, TableMetadata)
            if table.table_name in self.tables:
                raise MetadataValidationError("Duplicate table metadata")
            self.tables[table.table_name] = table
        self._validate_references()

    def _validate_references(self) -> None:
        tables = {table.name: table for table in self.schema.tables}
        columns = {
            f"{table.name}.{column.name}": column
            for table in self.schema.tables
            for column in table.columns
        }
        for name in [*self.scope.excluded_tables, *self.scope.preferred_tables, *self.tables]:
            if name not in tables:
                raise MetadataValidationError(f"Unknown table: {name}")
        for profile in self.profiles.values():
            if profile.profile:
                raise MetadataValidationError("Shared profiles must not inherit another profile")
        for name, metadata in self.tables.items():
            known_columns = {column.name for column in tables[name].columns}
            for column_name, column in metadata.columns.items():
                if column_name not in known_columns:
                    raise MetadataValidationError(f"Unknown column: {name}.{column_name}")
                if column.profile and column.profile not in self.profiles:
                    raise MetadataValidationError(f"Unknown profile: {column.profile}")
                effective = self.resolve_column(name, column_name)
                raw = columns[f"{name}.{column_name}"]
                if effective.date:
                    if effective.date.storage == "text" and raw.data_type not in {
                        "varchar",
                        "char",
                        "text",
                        "tinytext",
                        "mediumtext",
                        "longtext",
                    }:
                        raise MetadataValidationError(
                            "Text date profile applied to a native column"
                        )
                    if effective.date.storage == "native" and raw.data_type not in {
                        "date",
                        "datetime",
                        "timestamp",
                        "time",
                    }:
                        raise MetadataValidationError(
                            "Native date profile applied to a text column"
                        )
                if effective.values and effective.verified:
                    if not effective.value_encoding:
                        raise MetadataValidationError("Verified codes require value_encoding")
                    if effective.value_encoding == "numeric":
                        if raw.data_type not in {"bit", "tinyint", "smallint", "int", "bigint"}:
                            raise MetadataValidationError(
                                "Numeric flag mapping applied to text column"
                            )
                        if any(not key.lstrip("-").isdigit() for key in effective.values):
                            raise MetadataValidationError(
                                "Numeric flag codes must be integer strings"
                            )
                    elif raw.data_type not in {"varchar", "char", "text", "enum"}:
                        raise MetadataValidationError("Text flag mapping applied to numeric column")
        for term in self.terms.values():
            self._check_tables(term.related_tables, tables)
        for metric in self.metrics.values():
            for metric_column in [
                *metric.required_columns,
                *(f.column for f in metric.required_filters),
            ]:
                if metric_column not in columns:
                    raise MetadataValidationError(f"Unknown metric column: {metric_column}")
        for relationship in self.relationships:
            for reference in (relationship.from_, relationship.to):
                if f"{reference.table}.{reference.column}" not in columns:
                    raise MetadataValidationError("Unknown logical relationship endpoint")
        for example in self.examples:
            self._check_tables(example.related_tables, tables)

    @staticmethod
    def _check_tables(names: list[str], tables: dict[str, Any]) -> None:
        if set(names) - tables.keys():
            raise MetadataValidationError("Unknown referenced business table")

    def resolve_column(self, table: str, column: str) -> ColumnMetadata:
        local = self.tables[table].columns[column]
        if not local.profile:
            return local
        profile = self.profiles.get(local.profile)
        if profile is None:
            raise MetadataValidationError(f"Unknown profile: {local.profile}")
        data = profile.model_dump()
        data.update(local.model_dump(exclude_unset=True))
        data["profile"] = (
            None  # Validate the fully resolved definition without deferred inheritance.
        )
        data["verified"] = local.verified and profile.verified
        data["queryable"] = local.queryable and profile.queryable
        return ColumnMetadata.model_validate(data)

    def approved_tables(self) -> dict[str, TableMetadata]:
        return {
            name: table
            for name, table in self.tables.items()
            if table.verified and table.queryable and not self.scope.excludes(name)
        }

    def approved_columns(self, table: str) -> dict[str, ColumnMetadata]:
        if table not in self.approved_tables():
            return {}
        result = {name: self.resolve_column(table, name) for name in self.tables[table].columns}
        return {
            name: column for name, column in result.items() if column.verified and column.queryable
        }

    def approved_metrics(self) -> dict[str, MetricMetadata]:
        columns = {
            f"{table}.{name}"
            for table in self.approved_tables()
            for name in self.approved_columns(table)
        }
        return {
            name: metric
            for name, metric in self.metrics.items()
            if metric.verified
            and set([*metric.required_columns, *(f.column for f in metric.required_filters)])
            <= columns
        }

    def approved_terms(self) -> dict[str, GlossaryEntry]:
        tables = set(self.approved_tables())
        return {
            name: term
            for name, term in self.terms.items()
            if term.verified and set(term.related_tables) <= tables
        }

    def approved_relationships(self) -> list[LogicalRelationship]:
        columns = {
            f"{table}.{name}"
            for table in self.approved_tables()
            for name in self.approved_columns(table)
        }
        return [
            relationship
            for relationship in self.relationships
            if relationship.verified
            and f"{relationship.from_.table}.{relationship.from_.column}" in columns
            and f"{relationship.to.table}.{relationship.to.column}" in columns
        ]

    def approved_examples(self) -> list[VerifiedQueryExample]:
        tables = set(self.approved_tables())
        return [
            example
            for example in self.examples
            if example.verified and set(example.related_tables) <= tables
        ]

    def status(self) -> dict[str, Any]:
        return {
            "schema_tables": len(self.schema.tables),
            "excluded_tables": [
                table.name for table in self.schema.tables if self.scope.excludes(table.name)
            ],
            "table_files": len(self.tables),
            "draft_table_files": sum(not table.verified for table in self.tables.values()),
            "approved_tables": list(self.approved_tables()),
            "approved_column_count": sum(
                len(self.approved_columns(t)) for t in self.approved_tables()
            ),
            "approved_metrics": list(self.approved_metrics()),
            "missing_table_files": [
                table.name
                for table in self.schema.tables
                if not self.scope.excludes(table.name) and table.name not in self.tables
            ],
        }
