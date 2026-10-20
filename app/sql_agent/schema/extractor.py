from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection, text

from app.db.mysql import MySQLDatabase
from app.sql_agent.schema.models import (
    ColumnSchema,
    ConstraintSchema,
    ForeignKeySchema,
    IndexPart,
    IndexSchema,
    KeySchema,
    RawSchemaCatalog,
    SchemaCapabilities,
    TableSchema,
)

Row = Mapping[str, Any]

SCHEMA_QUERY = """
SELECT SCHEMA_NAME AS schema_name, DEFAULT_CHARACTER_SET_NAME AS default_character_set,
       DEFAULT_COLLATION_NAME AS default_collation
FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :schema
"""
CAPABILITIES_QUERY = """
SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'information_schema'
AND TABLE_NAME IN ('STATISTICS', 'TABLE_CONSTRAINTS', 'CHECK_CONSTRAINTS')
"""
TABLES_QUERY = """
SELECT TABLE_NAME AS name, TABLE_TYPE AS table_type, ENGINE AS engine,
       TABLE_COLLATION AS collation, TABLE_COMMENT AS comment
FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = :schema
ORDER BY TABLE_NAME
"""
COLUMNS_QUERY = """
SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, ORDINAL_POSITION AS ordinal_position,
       DATA_TYPE AS data_type, COLUMN_TYPE AS column_type, IS_NULLABLE AS is_nullable,
       COLUMN_DEFAULT AS default_value, COLUMN_COMMENT AS comment, EXTRA AS extra,
       GENERATION_EXPRESSION AS generation_expression, CHARACTER_SET_NAME AS character_set,
       COLLATION_NAME AS collation, CHARACTER_MAXIMUM_LENGTH AS character_maximum_length,
       NUMERIC_PRECISION AS numeric_precision, NUMERIC_SCALE AS numeric_scale,
       DATETIME_PRECISION AS datetime_precision
FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = :schema
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""
KEYS_QUERY = """
SELECT TABLE_NAME AS table_name, CONSTRAINT_NAME AS name, COLUMN_NAME AS column_name,
       ORDINAL_POSITION AS ordinal_position,
       POSITION_IN_UNIQUE_CONSTRAINT AS referenced_unique_position,
       REFERENCED_TABLE_SCHEMA AS referenced_schema, REFERENCED_TABLE_NAME AS referenced_table,
       REFERENCED_COLUMN_NAME AS referenced_column
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = :schema
ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
"""
REFERENCES_QUERY = """
SELECT TABLE_NAME AS table_name, CONSTRAINT_NAME AS name,
       UNIQUE_CONSTRAINT_NAME AS referenced_unique_constraint,
       UPDATE_RULE AS update_rule, DELETE_RULE AS delete_rule, MATCH_OPTION AS match_option
FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = :schema
ORDER BY TABLE_NAME, CONSTRAINT_NAME
"""


class SchemaExtractionError(RuntimeError):
    """Metadata is missing, inconsistent, unsupported, or inaccessible."""


def _query(connection: Connection, sql: str, schema: str) -> list[Row]:
    # SQL strings are fixed implementation constants; the database name is always bound.
    return [dict(row) for row in connection.execute(text(sql), {"schema": schema}).mappings()]


def _group(rows: list[Row], field: str) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[row[field]].append(row)
    return grouped


def _flag(value: Any, yes: Any, no: Any) -> bool:
    if value not in (yes, no):
        raise SchemaExtractionError("Unsupported metadata flag value")
    return bool(value == yes)


class SchemaExtractor:
    """Read technical facts only from MySQL INFORMATION_SCHEMA; never infer business meaning."""

    def __init__(self, database: MySQLDatabase) -> None:
        self.database = database

    def extract(self) -> RawSchemaCatalog:
        schema = self.database.settings.mysql_database
        with self.database.read_only_connection() as connection:
            schema_rows = _query(connection, SCHEMA_QUERY, schema)
            if len(schema_rows) != 1:
                raise SchemaExtractionError("Target database is missing or inaccessible")
            version = str(connection.execute(text("SELECT VERSION()")).scalar_one())
            available = {
                (row["table_name"].upper(), row["column_name"].upper())
                for row in _query(connection, CAPABILITIES_QUERY, schema)
            }
            capabilities = SchemaCapabilities(
                index_expressions=("STATISTICS", "EXPRESSION") in available,
                index_visibility=("STATISTICS", "IS_VISIBLE") in available,
                check_constraints=("CHECK_CONSTRAINTS", "CHECK_CLAUSE") in available,
                constraint_enforcement=("TABLE_CONSTRAINTS", "ENFORCED") in available,
            )
            tables = _query(connection, TABLES_QUERY, schema)
            if not tables:
                raise SchemaExtractionError("Target database contains no visible tables or views")
            columns = _query(connection, COLUMNS_QUERY, schema)
            keys = _query(connection, KEYS_QUERY, schema)
            references = _query(connection, REFERENCES_QUERY, schema)
            # Only capability-controlled constant fragments are interpolated, never user input.
            expression = "EXPRESSION" if capabilities.index_expressions else "NULL"
            visibility = "IS_VISIBLE" if capabilities.index_visibility else "NULL"
            indexes = _query(
                connection,
                f"""
                SELECT TABLE_NAME AS table_name, INDEX_NAME AS name, NON_UNIQUE AS non_unique,
                       INDEX_TYPE AS index_type, SEQ_IN_INDEX AS ordinal_position,
                       COLUMN_NAME AS column_name, SUB_PART AS prefix_length,
                       COLLATION AS collation, COMMENT AS comment, INDEX_COMMENT AS index_comment,
                       {expression} AS expression, {visibility} AS visibility
                FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA = :schema
                ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """,
                schema,
            )
            enforcement = "ENFORCED" if capabilities.constraint_enforcement else "NULL"
            constraints = _query(
                connection,
                f"""
                SELECT TABLE_NAME AS table_name, CONSTRAINT_NAME AS name,
                       CONSTRAINT_TYPE AS constraint_type, {enforcement} AS enforced
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = :schema
                ORDER BY TABLE_NAME, CONSTRAINT_NAME
            """,
                schema,
            )
            checks = (
                _query(
                    connection,
                    """
                SELECT CONSTRAINT_NAME AS name, CHECK_CLAUSE AS check_clause
                FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = :schema
                ORDER BY CONSTRAINT_NAME
            """,
                    schema,
                )
                if capabilities.check_constraints
                else []
            )
            return self._assemble(
                schema_rows[0],
                version,
                capabilities,
                tables,
                columns,
                keys,
                references,
                indexes,
                constraints,
                checks,
            )

    @staticmethod
    def _assemble(
        schema: Row,
        version: str,
        capabilities: SchemaCapabilities,
        tables: list[Row],
        columns: list[Row],
        keys: list[Row],
        references: list[Row],
        indexes: list[Row],
        constraints: list[Row],
        checks: list[Row],
    ) -> RawSchemaCatalog:
        table_names = {row["name"] for row in tables}
        for rows in (columns, keys, references, indexes, constraints):
            if any(row["table_name"] not in table_names for row in rows):
                raise SchemaExtractionError("Schema changed during extraction or is inconsistent")
        grouped_columns = _group(columns, "table_name")
        grouped_indexes = _group(indexes, "table_name")
        grouped_constraints = _group(constraints, "table_name")
        key_parts: dict[tuple[str, str], list[Row]] = defaultdict(list)
        for row in keys:
            key_parts[row["table_name"], row["name"]].append(row)
        rules = {(row["table_name"], row["name"]): row for row in references}
        check_clauses = {row["name"]: row["check_clause"] for row in checks}
        constraint_names = {(row["table_name"], row["name"]) for row in constraints}
        if set(key_parts) - constraint_names or set(rules) - constraint_names:
            raise SchemaExtractionError("Key metadata has no matching constraint")
        if set(check_clauses) - {
            row["name"] for row in constraints if row["constraint_type"] == "CHECK"
        }:
            raise SchemaExtractionError("CHECK clause has no matching constraint")
        if len(rules) != len(references) or len(check_clauses) != len(checks):
            raise SchemaExtractionError("Duplicate constraint metadata")
        result: list[TableSchema] = []
        for table in sorted(tables, key=lambda row: row["name"]):
            name = table["name"]
            table_columns = []
            for row in sorted(
                grouped_columns.get(name, []), key=lambda row: row["ordinal_position"]
            ):
                attributes = dict(row)
                attributes.pop("table_name")
                attributes["nullable"] = _flag(attributes.pop("is_nullable"), "YES", "NO")
                default = attributes.pop("default_value")
                attributes["default"] = str(default) if default is not None else None
                table_columns.append(ColumnSchema.model_validate(attributes))
            primary_key = None
            unique_keys = []
            foreign_keys = []
            table_constraints = []
            for row in sorted(grouped_constraints.get(name, []), key=lambda row: row["name"]):
                parts = sorted(
                    key_parts.get((name, row["name"]), []),
                    key=lambda part: part["ordinal_position"],
                )
                if [part["ordinal_position"] for part in parts] != list(range(1, len(parts) + 1)):
                    raise SchemaExtractionError("Constraint column sequence is incomplete")
                key_columns = [part["column_name"] for part in parts]
                clause = check_clauses.get(row["name"])
                if (
                    row["constraint_type"] == "CHECK"
                    and capabilities.check_constraints
                    and not clause
                ):
                    raise SchemaExtractionError("CHECK constraint clause is missing")
                table_constraints.append(
                    ConstraintSchema(
                        name=row["name"],
                        constraint_type=row["constraint_type"],
                        columns=key_columns,
                        check_clause=clause,
                        enforced=_flag(row["enforced"], "YES", "NO")
                        if row["enforced"] is not None
                        else None,
                    )
                )
                if row["constraint_type"] == "PRIMARY KEY":
                    if primary_key is not None:
                        raise SchemaExtractionError("Multiple primary keys reported")
                    primary_key = KeySchema(name=row["name"], columns=key_columns)
                elif row["constraint_type"] == "UNIQUE":
                    unique_keys.append(KeySchema(name=row["name"], columns=key_columns))
                elif row["constraint_type"] == "FOREIGN KEY":
                    rule = rules.get((name, row["name"]))
                    if not rule or not parts:
                        raise SchemaExtractionError("Foreign key metadata is incomplete")
                    if any(
                        part["referenced_table"] != parts[0]["referenced_table"]
                        or part["referenced_schema"] != parts[0]["referenced_schema"]
                        for part in parts
                    ):
                        raise SchemaExtractionError("Foreign key targets are inconsistent")
                    foreign_keys.append(
                        ForeignKeySchema(
                            name=row["name"],
                            columns=key_columns,
                            referenced_schema=parts[0]["referenced_schema"],
                            referenced_table=parts[0]["referenced_table"],
                            referenced_columns=[part["referenced_column"] for part in parts],
                            referenced_unique_positions=[
                                part["referenced_unique_position"] for part in parts
                            ],
                            referenced_unique_constraint=rule["referenced_unique_constraint"],
                            update_rule=rule["update_rule"],
                            delete_rule=rule["delete_rule"],
                            match_option=rule["match_option"],
                        )
                    )
            table_indexes = []
            for index_name, parts in sorted(_group(grouped_indexes.get(name, []), "name").items()):
                ordered = sorted(parts, key=lambda row: row["ordinal_position"])
                first = ordered[0]
                table_indexes.append(
                    IndexSchema(
                        name=index_name,
                        unique=_flag(first["non_unique"], 0, 1),
                        index_type=first["index_type"],
                        comment=first["comment"],
                        index_comment=first["index_comment"],
                        visible=_flag(first["visibility"], "YES", "NO")
                        if first["visibility"] is not None
                        else None,
                        parts=[
                            IndexPart(
                                ordinal_position=part["ordinal_position"],
                                column=part["column_name"],
                                expression=part["expression"],
                                prefix_length=part["prefix_length"],
                                collation=part["collation"],
                            )
                            for part in ordered
                        ],
                    )
                )
            result.append(
                TableSchema(
                    **dict(table),
                    columns=table_columns,
                    primary_key=primary_key,
                    unique_keys=unique_keys,
                    foreign_keys=foreign_keys,
                    indexes=table_indexes,
                    constraints=table_constraints,
                )
            )
        return RawSchemaCatalog(
            **dict(schema), server_version=version, capabilities=capabilities, tables=result
        )
