from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnSchema(SchemaModel):
    name: str = Field(min_length=1)
    ordinal_position: int = Field(ge=1)
    data_type: str
    column_type: str
    nullable: bool
    default: str | None
    comment: str = ""
    extra: str = ""
    generation_expression: str = ""
    character_set: str | None = None
    collation: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    datetime_precision: int | None = None


class IndexPart(SchemaModel):
    ordinal_position: int = Field(ge=1)
    column: str | None = None
    expression: str | None = None
    prefix_length: int | None = None
    collation: Literal["A", "D"] | None = None

    @model_validator(mode="after")
    def one_reference(self) -> Self:
        if bool(self.column) == bool(self.expression):
            raise ValueError("Index part must reference exactly one column or expression")
        return self


class IndexSchema(SchemaModel):
    name: str
    unique: bool
    index_type: str
    comment: str = ""
    index_comment: str = ""
    visible: bool | None = None
    parts: list[IndexPart] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_parts(self) -> Self:
        if [part.ordinal_position for part in self.parts] != list(range(1, len(self.parts) + 1)):
            raise ValueError("Index parts must be complete and ordered")
        return self


class KeySchema(SchemaModel):
    name: str
    columns: list[str] = Field(min_length=1)


class ForeignKeySchema(KeySchema):
    referenced_schema: str
    referenced_table: str
    referenced_columns: list[str] = Field(min_length=1)
    referenced_unique_constraint: str
    referenced_unique_positions: list[int] = Field(min_length=1)
    update_rule: str
    delete_rule: str
    match_option: str

    @model_validator(mode="after")
    def matching_arity(self) -> Self:
        if len(self.columns) != len(self.referenced_columns) or len(self.columns) != len(
            self.referenced_unique_positions
        ):
            raise ValueError("Foreign key column counts do not match")
        return self


class ConstraintSchema(SchemaModel):
    name: str
    constraint_type: Literal["PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK"]
    columns: list[str] = Field(default_factory=list)
    check_clause: str | None = None
    enforced: bool | None = None


def require_unique(values: list[str], description: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {description}")


class TableSchema(SchemaModel):
    name: str
    table_type: Literal["BASE TABLE", "VIEW"]
    engine: str | None = None
    collation: str | None = None
    comment: str = ""
    columns: list[ColumnSchema] = Field(min_length=1)
    primary_key: KeySchema | None = None
    unique_keys: list[KeySchema] = Field(default_factory=list)
    indexes: list[IndexSchema] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySchema] = Field(default_factory=list)
    constraints: list[ConstraintSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        names = [column.name for column in self.columns]
        require_unique(names, "columns")
        if [column.ordinal_position for column in self.columns] != list(
            range(1, len(self.columns) + 1)
        ):
            raise ValueError("Columns must be complete and ordered")
        for collection in (self.indexes, self.foreign_keys, self.unique_keys, self.constraints):
            require_unique([item.name for item in collection], "metadata identifiers")
        references = [part.column for index in self.indexes for part in index.parts if part.column]
        keys: list[KeySchema] = [*self.unique_keys, *self.foreign_keys]
        if self.primary_key:
            keys.append(self.primary_key)
        for key in keys:
            require_unique(key.columns, "key columns")
            references.extend(key.columns)
        references.extend(
            column for constraint in self.constraints for column in constraint.columns
        )
        if set(references) - set(names):
            raise ValueError("Metadata references missing local columns")
        constraints = {constraint.name: constraint for constraint in self.constraints}
        indexes = {index.name: index for index in self.indexes}
        if {key.name for key in keys} != {
            constraint.name
            for constraint in self.constraints
            if constraint.constraint_type != "CHECK"
        }:
            raise ValueError("Constraints and keys must describe the same objects")
        for key in keys:
            constraint = constraints.get(key.name)
            expected_type = (
                "FOREIGN KEY"
                if isinstance(key, ForeignKeySchema)
                else "PRIMARY KEY"
                if key is self.primary_key
                else "UNIQUE"
            )
            if not constraint or constraint.constraint_type != expected_type:
                raise ValueError("Key has no matching constraint")
            if constraint.columns != key.columns:
                raise ValueError("Key/constraint column order mismatch")
            if not isinstance(key, ForeignKeySchema):
                index = indexes.get(key.name)
                if not index or not index.unique:
                    raise ValueError("Primary/unique key has no matching unique index")
                if [part.column for part in index.parts] != key.columns:
                    raise ValueError("Key/index column order mismatch")
        return self


class SchemaCapabilities(SchemaModel):
    index_expressions: bool
    index_visibility: bool
    check_constraints: bool
    constraint_enforcement: bool


class RawSchemaCatalog(SchemaModel):
    format_version: Literal[1] = 1
    source: Literal["mysql_information_schema"] = "mysql_information_schema"
    schema_name: str
    server_version: str
    default_character_set: str
    default_collation: str
    capabilities: SchemaCapabilities
    tables: list[TableSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relationship_targets(self) -> Self:
        require_unique([table.name for table in self.tables], "tables")
        tables = {table.name: table for table in self.tables}
        for table in self.tables:
            for fk in table.foreign_keys:
                if fk.referenced_schema != self.schema_name:
                    continue  # Preserve cross-schema facts; do not retrieve the other schema.
                target = tables.get(fk.referenced_table)
                if target is None:
                    raise ValueError("Foreign key references a missing table in the target schema")
                if set(fk.referenced_columns) - {column.name for column in target.columns}:
                    raise ValueError("Foreign key references missing target columns")
                index = next(
                    (
                        index
                        for index in target.indexes
                        if index.name == fk.referenced_unique_constraint
                    ),
                    None,
                )
                if index is None:
                    raise ValueError("Foreign key references a missing target index")
                for column, position in zip(
                    fk.referenced_columns, fk.referenced_unique_positions, strict=True
                ):
                    if (
                        not 1 <= position <= len(index.parts)
                        or index.parts[position - 1].column != column
                    ):
                        raise ValueError("Foreign key target index positions do not match")
        return self
