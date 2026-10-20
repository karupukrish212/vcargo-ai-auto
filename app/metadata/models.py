from datetime import date
from fnmatch import fnmatchcase
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator


class MetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ReviewedMetadata(MetadataModel):
    verified: StrictBool = False
    source: Literal["manual", "user_provided", "schema_generated", "llm_generated"] = "manual"
    reviewed_by: str | None = None
    reviewed_on: date | None = None
    notes: str = ""

    @model_validator(mode="after")
    def require_review(self) -> Self:
        if self.verified:
            if self.source in {"schema_generated", "llm_generated"}:
                raise ValueError("Generated drafts require an explicit manual review source")
            if not self.reviewed_by or not self.reviewed_by.strip() or self.reviewed_on is None:
                raise ValueError("Verified metadata requires reviewed_by and reviewed_on")
        return self


class DateSemantics(MetadataModel):
    storage: Literal["text", "native"]
    mysql_format: str | None = None
    timezone: str | None = None
    invalid_value_policy: Literal["fail_closed"] = "fail_closed"

    @model_validator(mode="after")
    def validate_format_and_timezone(self) -> Self:
        if self.storage == "text" and not self.mysql_format:
            raise ValueError("Text dates require a MySQL format")
        if self.storage == "native" and self.mysql_format is not None:
            raise ValueError("Native dates must not have a text parsing format")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Use a valid IANA timezone, for example Asia/Kolkata") from exc
        return self


class ColumnMetadata(ReviewedMetadata):
    profile: str | None = None
    business_name: str = ""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    semantic_type: str | None = None
    values: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    value_encoding: Literal["numeric", "text"] | None = None
    date: DateSemantics | None = None
    queryable: StrictBool = True

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified:
            if not self.profile and (
                not self.business_name.strip() or not self.description.strip()
            ):
                raise ValueError("Verified columns require a business name and description")
            if any(not meaning.strip() for meaning in self.values.values()):
                raise ValueError("Verified enum codes require nonempty business meanings")
            if self.date and not self.date.timezone:
                raise ValueError("Verified date definitions require the storage timezone")
            if self.semantic_type == "status_flag" and not self.values and not self.profile:
                raise ValueError("Verified flags require explicit code meanings")
        return self


class TableMetadata(ReviewedMetadata):
    table_name: str = Field(min_length=1)
    business_name: str = ""
    description: str = ""
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)
    queryable: StrictBool = True
    columns: dict[str, ColumnMetadata] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified and (
            not self.business_name.strip() or not self.description.strip() or not self.domain
        ):
            raise ValueError("Verified tables require a business name, description, and domain")
        return self


class TableScope(MetadataModel):
    excluded_patterns: list[str] = Field(default_factory=list)
    excluded_tables: list[str] = Field(default_factory=list)
    preferred_tables: list[str] = Field(default_factory=list)

    def excludes(self, table: str) -> bool:
        return table in self.excluded_tables or any(
            fnmatchcase(table, pattern) for pattern in self.excluded_patterns
        )


class GlossaryEntry(ReviewedMetadata):
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    related_tables: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified and (not self.description.strip() or not self.related_tables):
            raise ValueError("Verified glossary entries require a description and related tables")
        return self


class MetricFilter(MetadataModel):
    column: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "IN"] = "="
    value: str | int | float | bool | list[str | int | float | bool]


class MetricMetadata(ReviewedMetadata):
    business_name: str = ""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    expression: str = ""
    required_columns: list[str] = Field(default_factory=list)
    required_filters: list[MetricFilter] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified and not all(
            (
                self.business_name.strip(),
                self.description.strip(),
                self.expression.strip(),
                self.required_columns,
            )
        ):
            raise ValueError("Verified metrics require a described formula and explicit columns")
        return self


class ColumnReference(MetadataModel):
    table: str
    column: str


class LogicalRelationship(ReviewedMetadata):
    from_: ColumnReference = Field(alias="from")
    to: ColumnReference
    relationship: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"]
    type: Literal["logical"] = "logical"

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified and self.relationship == "unknown":
            raise ValueError("Verified logical relationships require reviewed cardinality")
        return self


class VerifiedQueryExample(ReviewedMetadata):
    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    question: str
    sql: str
    related_tables: list[str]
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_definition(self) -> Self:
        if self.verified and (
            not self.question.strip() or not self.sql.strip() or not self.related_tables
        ):
            raise ValueError("Verified examples require a question, SQL, and declared tables")
        return self


class ColumnProfiles(MetadataModel):
    profiles: dict[str, ColumnMetadata] = Field(default_factory=dict)


class GlossaryCatalog(MetadataModel):
    terms: dict[str, GlossaryEntry] = Field(default_factory=dict)


class MetricsCatalog(MetadataModel):
    metrics: dict[str, MetricMetadata] = Field(default_factory=dict)


class RelationshipsCatalog(MetadataModel):
    relationships: list[LogicalRelationship] = Field(default_factory=list)


class ExamplesCatalog(MetadataModel):
    queries: list[VerifiedQueryExample] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_example_ids(self) -> Self:
        ids = [query.id for query in self.queries if query.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("SQL example IDs must be unique")
        return self
