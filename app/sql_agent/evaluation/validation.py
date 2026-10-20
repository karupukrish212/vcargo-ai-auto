"""A deliberately small SQL subset for isolated example tests, not an API SQL policy."""

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.metadata.loader import BusinessCatalog
from app.metadata.models import VerifiedQueryExample


class ExampleValidationError(ValueError):
    """An example cannot safely be tested using the synthetic SELECT runner."""


@dataclass
class ExampleKnowledge:
    column_types: dict[str, dict[str, str]]
    primary_keys: dict[str, tuple[str, ...]]
    foreign_keys: set[tuple[str, str, str, str]]
    enum_values: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    @classmethod
    def from_catalog(cls, catalog: BusinessCatalog) -> "ExampleKnowledge":
        raw = {table.name: table for table in catalog.schema.tables}
        approved = {name: catalog.approved_columns(name) for name in catalog.approved_tables()}
        types = {
            table: {c.name: c.data_type for c in raw[table].columns if c.name in columns}
            for table, columns in approved.items()
        }
        return cls(
            column_types=types,
            primary_keys={
                name: tuple(table.primary_key.columns) if table.primary_key else ()
                for name, table in raw.items()
                if name in types
            },
            foreign_keys={
                (name, fk.columns[0], fk.referenced_table, fk.referenced_columns[0])
                for name, table in raw.items()
                for fk in table.foreign_keys
                if name in types
                and fk.referenced_schema == catalog.schema.schema_name
                and len(fk.columns) == len(fk.referenced_columns) == 1
                and fk.referenced_table in types
                and fk.columns[0] in types[name]
                and fk.referenced_columns[0] in types[fk.referenced_table]
            },
            enum_values={
                (table, name): set(column.values)
                for table, columns in approved.items()
                for name, column in columns.items()
                if column.values
            },
        )


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_NODES = {
    "Select",
    "Alias",
    "And",
    "Case",
    "Column",
    "Count",
    "DateAdd",
    "EQ",
    "From",
    "GTE",
    "GT",
    "Group",
    "Identifier",
    "If",
    "In",
    "Interval",
    "Is",
    "Join",
    "LT",
    "LTE",
    "Like",
    "Limit",
    "Literal",
    "Lower",
    "Null",
    "Order",
    "Ordered",
    "Select",
    "Star",
    "Sub",
    "Sum",
    "Table",
    "TableAlias",
    "TsOrDsToDate",
    "UtcTimestamp",
    "Var",
    "Where",
    "Add",
    "Paren",
}
_SELECT_ARGS = {"expressions", "from_", "joins", "where", "group", "order", "limit"}


def checked_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ExampleValidationError("Unsupported identifier")
    return value


def _column_reference(column: exp.Column, aliases: dict[str, str]) -> tuple[str, str]:
    if column.db or column.catalog or column.table not in aliases:
        raise ExampleValidationError("Columns must use a known table alias")
    return aliases[column.table], column.name


def _enum_operand(
    value: exp.Expression, aliases: dict[str, str], knowledge: ExampleKnowledge
) -> set[str] | None:
    lower = isinstance(value, exp.Lower)
    column = value.this if lower else value
    if not isinstance(column, exp.Column) or not column.table:
        return None
    values = knowledge.enum_values.get(_column_reference(column, aliases))
    return {item.lower() for item in values} if lower and values else values


def validate_example(example: VerifiedQueryExample, knowledge: ExampleKnowledge) -> exp.Select:
    if len(example.sql) > 20000:
        raise ExampleValidationError("Example SQL is too long")
    try:
        statements = sqlglot.parse(example.sql, read="mysql")
    except (ParseError, ValueError) as exc:
        raise ExampleValidationError("Example is not parseable MySQL SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ExampleValidationError("Exactly one SELECT is required")
    tree = statements[0]
    if any(value for key, value in tree.args.items() if key not in _SELECT_ARGS):
        raise ExampleValidationError("Unsupported SELECT clause")
    for node in tree.walk():
        if type(node).__name__ not in _ALLOWED_NODES or node.comments:
            raise ExampleValidationError("Unsupported SQL expression or comment")
        if isinstance(node, exp.Select) and node is not tree:
            raise ExampleValidationError("Nested SELECT is outside the fixture test subset")
        if isinstance(node, exp.Identifier):
            checked_identifier(node.name)
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise ExampleValidationError("Only COUNT(*) may use a star")
        if isinstance(node, exp.Var) and node.name.upper() not in {"DAY", "MINUTE"}:
            raise ExampleValidationError("Unsupported date interval")

    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if (
            table.db
            or table.catalog
            or any(value for key, value in table.args.items() if key not in {"this", "alias"})
        ):
            raise ExampleValidationError("Qualified or decorated tables are not allowed")
        if not isinstance(table.this, exp.Identifier) or table.name not in knowledge.column_types:
            raise ExampleValidationError("Table is not approved")
        alias = checked_identifier(table.alias_or_name)
        if alias in aliases:
            raise ExampleValidationError("Duplicate table alias")
        aliases[alias] = table.name
    if not aliases or set(aliases.values()) != set(example.related_tables):
        raise ExampleValidationError("Declared tables must exactly match the query")
    output_names = [expression.alias_or_name for expression in tree.expressions]
    if any(not name for name in output_names) or len(set(output_names)) != len(output_names):
        raise ExampleValidationError("Output names must be explicit and unique")
    for column in tree.find_all(exp.Column):
        if not column.table and column.name in output_names and column.find_ancestor(exp.Order):
            continue
        table, name = _column_reference(column, aliases)
        if name not in knowledge.column_types[table]:
            raise ExampleValidationError("Column is not approved")

    source = tree.args.get("from_")
    if not isinstance(source, exp.From) or not isinstance(source.this, exp.Table):
        raise ExampleValidationError("A base fixture table is required")
    seen = {source.this.alias_or_name}
    for join in tree.args.get("joins", []):
        if not isinstance(join, exp.Join) or not isinstance(join.this, exp.Table):
            raise ExampleValidationError("Unsupported join source")
        if any(value for key, value in join.args.items() if key not in {"this", "kind", "on"}):
            raise ExampleValidationError("Only INNER joins are supported by the fixture runner")
        if join.args.get("kind", "").upper() not in {"", "INNER"}:
            raise ExampleValidationError("Only INNER joins are supported by the fixture runner")
        condition = join.args.get("on")
        if not isinstance(condition, exp.EQ) or not all(
            isinstance(side, exp.Column) for side in (condition.this, condition.expression)
        ):
            raise ExampleValidationError("Join must match a declared single-column foreign key")
        new_alias = join.this.alias_or_name
        left, right = condition.this, condition.expression
        if right.table != new_alias:
            left, right = right, left
        if left.table not in seen or right.table != new_alias:
            raise ExampleValidationError("Join must reference the newly joined table")
        a, ac = _column_reference(left, aliases)
        b, bc = _column_reference(right, aliases)
        if (a, ac, b, bc) not in knowledge.foreign_keys or knowledge.primary_keys[b] != (bc,):
            raise ExampleValidationError("Join is not a declared many-to-one physical FK")
        seen.add(new_alias)

    for condition in tree.find_all(exp.EQ, exp.In):
        values = _enum_operand(condition.this, aliases, knowledge)
        operands = (
            condition.expressions if isinstance(condition, exp.In) else [condition.expression]
        )
        if values:
            for operand in operands:
                if isinstance(operand, exp.Literal) and operand.this not in values:
                    raise ExampleValidationError("Enum code is not approved")
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression
        if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
            raise ExampleValidationError("LIMIT must be an integer literal")
        if not 1 <= int(value.this) <= 1000:
            raise ExampleValidationError("LIMIT is outside the fixture runner bounds")
    return tree
