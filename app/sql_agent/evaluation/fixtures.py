"""Compile validated examples against literal-only MySQL CTEs."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlglot import exp

from app.metadata.models import VerifiedQueryExample
from app.sql_agent.evaluation.models import ExampleCase, ExampleSuite, Scalar
from app.sql_agent.evaluation.validation import (
    ExampleKnowledge,
    ExampleValidationError,
    checked_identifier,
    validate_example,
)

_CASTS = {
    "bigint": "SIGNED",
    "int": "SIGNED",
    "bit": "UNSIGNED",
    "decimal": "DECIMAL(19,2)",
    "varchar": "CHAR CHARACTER SET utf8mb4",
    "date": "DATE",
    "datetime": "DATETIME(6)",
}


@dataclass
class CompiledFixtureQuery:
    sql: str
    parameters: dict[str, Any]


def _typed_value(value: Scalar, kind: str) -> Any:
    if value is None:
        return None
    try:
        if kind in {"bigint", "int", "bit"}:
            if type(value) is not int:
                raise ValueError
            if kind == "bit" and value not in {0, 1}:
                raise ValueError
            bits = 32 if kind == "int" else 64
            if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                raise ValueError
            return value
        if kind == "decimal":
            number = Decimal(str(value))
            if not number.is_finite() or abs(number) >= Decimal("100000000000000000"):
                raise ValueError
            if number != number.quantize(Decimal("0.01")):
                raise ValueError
            return number
        if not isinstance(value, str):
            raise ValueError
        if kind == "date":
            return date.fromisoformat(value)
        if kind == "datetime":
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                raise ValueError
            return parsed
        if kind == "varchar" and len(value) <= 255:
            return value
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise ExampleValidationError("Fixture value does not match its declared type") from exc
    raise ExampleValidationError("Unsupported fixture value")


def compile_fixture_query(
    example: VerifiedQueryExample,
    suite: ExampleSuite,
    case: ExampleCase,
    knowledge: ExampleKnowledge,
) -> CompiledFixtureQuery:
    tree = validate_example(example, knowledge).copy()
    if example.id != case.example_id:
        raise ExampleValidationError("Fixture case references a different SQL example")
    parameters: dict[str, Any] = {}
    definitions: list[str] = []
    for table_name in sorted(set(example.related_tables)):
        if table_name not in suite.tables:
            raise ExampleValidationError("No synthetic table supplied for example")
        fixture = suite.tables[table_name]
        columns = list(fixture.columns)
        for name, kind in fixture.columns.items():
            checked_identifier(name)
            if knowledge.column_types[table_name].get(name) != kind:
                raise ExampleValidationError("Fixture column/type differs from approved schema")
        used = {
            node.name
            for node in tree.find_all(exp.Column)
            if node.table
            and next(
                (t.name for t in tree.find_all(exp.Table) if t.alias_or_name == node.table), None
            )
            == table_name
        }
        if not used <= fixture.columns.keys():
            raise ExampleValidationError("Fixture omits a queried column")
        primary_key = knowledge.primary_keys[table_name]
        if not set(primary_key) <= fixture.columns.keys():
            raise ExampleValidationError("Fixture must include the table primary key")
        seen: set[tuple[Any, ...]] = set()
        selects: list[str] = []
        rows = [] if table_name in case.empty_tables else fixture.rows
        for row in rows:
            effective = {
                name: _typed_value(
                    None if f"{table_name}.{name}" in case.null_columns else value, kind
                )
                for (name, kind), value in zip(fixture.columns.items(), row, strict=True)
            }
            key = tuple(effective[name] for name in primary_key)
            if not key or None in key or key in seen:
                raise ExampleValidationError("Fixture primary keys must be non-NULL and unique")
            seen.add(key)
            parts: list[str] = []
            for name, kind in fixture.columns.items():
                bind = f"fixture_{len(parameters)}"
                parameters[bind] = effective[name]
                cast = f"CAST(:{bind} AS {_CASTS[kind]})"
                if kind == "varchar":
                    cast += " COLLATE utf8mb4_0900_ai_ci"
                parts.append(f"{cast} AS `{name}`")
            selects.append("SELECT " + ", ".join(parts))
        if not selects:
            parts = [
                f"CAST(NULL AS {_CASTS[kind]}) AS `{name}`"
                for name, kind in fixture.columns.items()
            ]
            selects.append("SELECT " + ", ".join(parts) + " WHERE 1 = 0")
        name = checked_identifier(f"fixture_{table_name}")
        definitions.append(f"`{name}` AS (" + " UNION ALL ".join(selects) + ")")

    # Rewrite every source table; no original database table survives in FROM/JOIN.
    for table in tree.find_all(exp.Table):
        alias = table.alias_or_name
        replacement = checked_identifier(f"fixture_{table.name}")
        table.set("this", exp.to_identifier(replacement, quoted=True))
        if not table.alias:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
    # Freeze only the clock, keeping the query's India offset/date arithmetic intact.
    clock = case.utc_now.replace(tzinfo=None).isoformat(sep=" ")
    for node in list(tree.find_all(exp.UtcTimestamp)):
        node.replace(exp.Cast(this=exp.Literal.string(clock), to=exp.DataType.build("DATETIME")))
    sql = "WITH " + ",\n".join(definitions) + "\n" + tree.sql(dialect="mysql")
    return CompiledFixtureQuery(sql=sql, parameters=parameters)
