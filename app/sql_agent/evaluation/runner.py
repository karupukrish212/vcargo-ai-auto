import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.mysql import MySQLDatabase
from app.metadata.models import VerifiedQueryExample
from app.sql_agent.evaluation.fixtures import CompiledFixtureQuery, compile_fixture_query
from app.sql_agent.evaluation.models import ExampleCase, ExampleSuite, ResultExpectation, Scalar
from app.sql_agent.evaluation.validation import ExampleKnowledge, ExampleValidationError


def _serialize(value: Any) -> Scalar:
    if value is None or type(value) is int or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    raise ExampleValidationError("Unexpected MySQL fixture result type")


def results_match(
    columns: list[str], rows: list[list[Scalar]], expected: ResultExpectation
) -> bool:
    if columns != expected.columns:
        return False
    if expected.ordered:
        return rows == expected.rows
    encode = lambda row: json.dumps(row, ensure_ascii=False)  # noqa: E731
    return sorted(map(encode, rows)) == sorted(map(encode, expected.rows))


def prepare_suite(
    examples: list[VerifiedQueryExample], suite: ExampleSuite, knowledge: ExampleKnowledge
) -> list[tuple[ExampleCase, CompiledFixtureQuery]]:
    if any(example.id is None for example in examples):
        raise ExampleValidationError("Assign a stable ID to every SQL example before testing")
    by_id = {example.id: example for example in examples}
    if len(by_id) != len(examples):
        raise ExampleValidationError("Duplicate SQL example ID")
    if {case.example_id for case in suite.cases} != set(by_id):
        raise ExampleValidationError(
            "Every SQL example must have cases; unknown example IDs are invalid"
        )
    return [
        (case, compile_fixture_query(by_id[case.example_id], suite, case, knowledge))
        for case in suite.cases
    ]


def run_mysql_suite(
    database: MySQLDatabase,
    examples: list[VerifiedQueryExample],
    suite: ExampleSuite,
    knowledge: ExampleKnowledge,
) -> dict[str, Any]:
    prepared = prepare_suite(examples, suite, knowledge)  # Validate everything before connecting.
    cases: list[dict[str, Any]] = []
    with database.read_only_connection() as connection:
        version = str(connection.execute(text("SELECT VERSION()")).scalar_one())
        original_mode = str(connection.execute(text("SELECT @@SESSION.sql_mode")).scalar_one())
        modes = set(original_mode.split(",")) | {"ONLY_FULL_GROUP_BY", "STRICT_TRANS_TABLES"}
        connection.execute(text("SET SESSION sql_mode = :mode"), {"mode": ",".join(sorted(modes))})
        try:
            for case, compiled in prepared:
                connection.execute(text("EXPLAIN " + compiled.sql), compiled.parameters).all()
                result = connection.execute(text(compiled.sql), compiled.parameters)
                columns = list(result.keys())
                raw_rows = result.fetchmany(1001)
                if len(raw_rows) > 1000:
                    raise ExampleValidationError("Fixture result exceeds the row bound")
                rows = [[_serialize(value) for value in row] for row in raw_rows]
                matched = results_match(columns, rows, case.expected)
                cases.append(
                    {
                        "id": case.id,
                        "example_id": case.example_id,
                        "status": "passed" if matched else "failed",
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                    }
                )
        finally:
            connection.execute(text("SET SESSION sql_mode = :mode"), {"mode": original_mode})
    return {
        "status": "passed" if all(case["status"] == "passed" for case in cases) else "failed",
        "engine": "mysql",
        "engine_version": version,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "example_count": len(examples),
        "case_count": len(cases),
        "synthetic_data_only": True,
        "expert_verification_changed": False,
        "query_sha256": {
            example.id or "": hashlib.sha256(example.sql.encode()).hexdigest()
            for example in examples
        },
        "fixture_sha256": hashlib.sha256(suite.model_dump_json().encode()).hexdigest(),
        "cases": cases,
    }
