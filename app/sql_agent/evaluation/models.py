from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

Scalar = StrictStr | StrictInt | None
FixtureType = Literal["bigint", "int", "bit", "decimal", "varchar", "date", "datetime"]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FixtureTable(EvaluationModel):
    columns: dict[str, FixtureType]
    rows: list[list[Scalar]] = Field(max_length=100)

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        if not 1 <= len(self.columns) <= 64:
            raise ValueError("Fixture tables require between 1 and 64 columns")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Fixture row width does not match its columns")
        return self


class ResultExpectation(EvaluationModel):
    columns: list[str]
    rows: list[list[Scalar]] = Field(max_length=1000)
    ordered: bool = True

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Expected output column names must be unique")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Expected result width does not match its columns")
        return self


class ExampleCase(EvaluationModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    example_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    utc_now: datetime
    empty_tables: list[str] = Field(default_factory=list)
    null_columns: list[str] = Field(default_factory=list)
    expected: ResultExpectation

    @model_validator(mode="after")
    def utc_clock(self) -> Self:
        if self.utc_now.utcoffset() != timedelta(0):
            raise ValueError("Fixture clock must carry an explicit UTC offset")
        return self


class ExampleSuite(EvaluationModel):
    format_version: Literal[1] = 1
    description: str
    tables: dict[str, FixtureTable]
    cases: list[ExampleCase]

    @model_validator(mode="after")
    def valid_cases(self) -> Self:
        ids = [case.id for case in self.cases]
        if not self.cases or len(ids) != len(set(ids)):
            raise ValueError("Fixture case IDs must be nonempty and unique")
        for case in self.cases:
            if set(case.empty_tables) - self.tables.keys():
                raise ValueError("Unknown empty fixture table")
            for reference in case.null_columns:
                table, separator, column = reference.partition(".")
                if (
                    not separator
                    or table not in self.tables
                    or column not in self.tables[table].columns
                ):
                    raise ValueError("Unknown NULL fixture column")
        return self
