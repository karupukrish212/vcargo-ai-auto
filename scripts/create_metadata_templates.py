"""Create blank review files from technical facts; never overwrite existing expert edits."""

import argparse
import json
from pathlib import Path

import yaml

from app.metadata.loader import read_yaml
from app.metadata.models import TableScope
from app.sql_agent.schema.catalog import load_catalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create unverified per-table YAML review templates"
    )
    parser.add_argument("--schema", type=Path, default=Path("metadata/generated/schema_raw.json"))
    parser.add_argument("--metadata", type=Path, default=Path("metadata"))
    args = parser.parse_args()
    schema = load_catalog(args.schema)
    scope = read_yaml(args.metadata / "policies/table_scope.yaml", TableScope)
    directory = args.metadata / "tables"
    directory.mkdir(parents=True, exist_ok=True)
    created = skipped = 0
    for table in schema.tables:
        if scope.excludes(table.name):
            continue
        # A database identifier must never become an unchecked filesystem path.
        if Path(table.name).name != table.name or any(char in table.name for char in "/\\:"):
            raise ValueError("Table identifier is unsafe for a metadata filename")
        path = directory / f"{table.name}.yaml"
        if path.exists():
            skipped += 1
            continue
        columns = {}
        for column in table.columns:
            entry: dict[str, object] = {
                "business_name": "",
                "description": "",
                "aliases": [],
                "verified": False,
                "source": "schema_generated",
            }
            if column.name in {"createdon", "modifiedon"}:
                profile = "audit_created" if column.name == "createdon" else "audit_modified"
                suffix = "text" if column.data_type in {"varchar", "char", "text"} else "native"
                entry["profile"] = f"{profile}_{suffix}"
            elif column.name in {"active", "cancel"}:
                entry["profile"] = f"{column.name}_flag"
                entry["value_encoding"] = (
                    "text" if column.data_type in {"varchar", "char"} else "numeric"
                )
            elif column.name in {
                "createdby",
                "modifiedby",
                "orgid",
                "branchcode",
                "branchname",
                "userid",
            }:
                entry["profile"] = {
                    "createdby": "created_by_user",
                    "modifiedby": "modified_by_user",
                    "orgid": "organisation_reference",
                    "branchcode": "branch_code",
                    "branchname": "branch_name",
                    "userid": "related_user",
                }[column.name]
            if "profile" in entry:
                # Omitted fields inherit shared descriptions. Empty overrides would erase them.
                for field in ("business_name", "description", "aliases"):
                    entry.pop(field)
            if column.name.casefold() in {
                "password",
                "pwd",
                "client_secret",
                "authtoken",
                "encrypted_otp",
                "sek",
                "token",
            }:
                entry["queryable"] = False
                entry["notes"] = "Keep credential/secret fields outside report queries."
            columns[column.name] = entry
        data: dict[str, object] = {
            "table_name": table.name,
            "business_name": "",
            "description": "",
            "domain": None,
            "aliases": [],
            "queryable": True,
            "verified": False,
            "source": "schema_generated",
            "reviewed_by": None,
            "reviewed_on": None,
            "columns": columns,
        }
        content = (
            "# Editable business metadata. Review only relevant columns; this file is a draft.\n"
        )
        content += yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        created += 1
    print(json.dumps({"created": created, "existing_files_preserved": skipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
