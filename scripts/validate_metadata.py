import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.metadata.loader import BusinessCatalog, MetadataValidationError
from app.sql_agent.schema.catalog import load_catalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate business metadata without connecting to MySQL"
    )
    parser.add_argument("--metadata", type=Path, default=Path("metadata"))
    parser.add_argument("--schema", type=Path, default=Path("metadata/generated/schema_raw.json"))
    args = parser.parse_args()
    try:
        catalog = BusinessCatalog(args.metadata, load_catalog(args.schema))
        print(json.dumps({"status": "valid", **catalog.status()}, indent=2))
        return 0
    except ValidationError as exc:
        issues = [
            {"location": list(error["loc"]), "message": error["msg"]}
            for error in exc.errors(include_input=False, include_context=False)
        ]
        print(json.dumps({"status": "invalid", "issues": issues}, default=str, indent=2))
    except MetadataValidationError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}))
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "reason": "Metadata file unavailable",
                    "error_type": type(exc).__name__,
                }
            )
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
