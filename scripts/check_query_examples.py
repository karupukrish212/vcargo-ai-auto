"""Check example SQL using synthetic SELECT-only MySQL fixtures; never query ERP rows."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import Settings
from app.db.mysql import DatabaseSafetyError, MySQLDatabase
from app.metadata.loader import BusinessCatalog, MetadataValidationError, read_yaml
from app.sql_agent.evaluation.models import ExampleSuite
from app.sql_agent.evaluation.runner import prepare_suite, run_mysql_suite
from app.sql_agent.evaluation.validation import ExampleKnowledge, ExampleValidationError
from app.sql_agent.schema.catalog import load_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=Path("metadata"))
    parser.add_argument("--schema", type=Path, default=Path("metadata/generated/schema_raw.json"))
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures/query_examples.yaml"))
    parser.add_argument(
        "--mysql", action="store_true", help="Execute synthetic CTEs via the existing reader"
    )
    parser.add_argument("--output", type=Path, help="Write the complete synthetic result report")
    args = parser.parse_args()
    database: MySQLDatabase | None = None
    try:
        catalog = BusinessCatalog(args.metadata, load_catalog(args.schema))
        suite = read_yaml(args.fixtures, ExampleSuite)
        knowledge = ExampleKnowledge.from_catalog(catalog)
        prepared = prepare_suite(catalog.examples, suite, knowledge)
        if args.mysql:
            database = MySQLDatabase(Settings())
            report = run_mysql_suite(database, catalog.examples, suite, knowledge)
        else:
            report = {
                "status": "static_checks_passed",
                "example_count": len(catalog.examples),
                "case_count": len(prepared),
                "execution_checked": False,
                "expert_verification_changed": False,
            }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        summary = {
            key: value for key, value in report.items() if key not in {"cases", "query_sha256"}
        }
        if "cases" in report:
            summary["failed_cases"] = [
                case["id"] for case in report["cases"] if case["status"] != "passed"
            ]
        print(json.dumps(summary, indent=2))
        return 1 if report["status"] == "failed" else 0
    except (ExampleValidationError, MetadataValidationError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
    except (ValidationError, OSError, DatabaseSafetyError, SQLAlchemyError) as exc:
        # Do not print connection strings, SQL parameters, credentials, or raw driver errors.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "Example check could not complete",
                    "error_type": type(exc).__name__,
                }
            )
        )
    finally:
        if database is not None:
            database.dispose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
