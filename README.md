# ERP SQL Agent — Schema Foundation and Business Metadata

This repository implements the FastAPI/backend foundation and read-only MySQL
schema extraction, plus reviewed business metadata models, loaders, and editable
YAML templates. SQL generation, LLMs, RAG, LangGraph execution, automatic business
enrichment, and frontend features are not implemented yet.

Requires Python 3.12+ and MySQL 8.0+. The API can start without credentials;
`/health` reports liveness and `/ready` returns HTTP 503 until a safe database
connection is available. Run commands from the repository root.

## Local installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

If activation is unavailable, use `.\.venv\Scripts\python.exe` instead of `python`.
`requirements.txt` delegates to `pyproject.toml`; `pip install -r requirements.txt`
installs runtime dependencies. Development tools require the `dev` extra above.

## Supply credentials locally

Edit the untracked `.env` file with `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`,
`MYSQL_USER`, and `MYSQL_PASSWORD`. Quote passwords containing `#` or spaces.
Do not paste credentials into chat or commit `.env`. Environment variables take
precedence over `.env`. Use a DBA-provisioned dedicated account with schema-wide
SELECT and optionally SHOW VIEW, restricted to the ERP database and the agent's
network origin. Request a replica endpoint when available.

The account verifier requires explicit grants; it rejects privileged usernames,
writes, administrative privileges, global SELECT, other database grants, grant
options, and roles. A role-based account must be replaced with an explicitly
granted dedicated reader by your DBA. Table-only grants are rejected because
MySQL can hide inaccessible tables from INFORMATION_SCHEMA and produce a partial
catalog. This application never creates accounts or changes grants.

Database-level grants containing `_` or `%` must escape those characters (for
example, a DBA should grant on `` `erp\_test`.* `` for `erp_test`) when MySQL
`partial_revokes` is OFF. When that server setting is ON, MySQL treats the grant
name literally. The checker reads this setting and requires the corresponding
exact scope; it does not change it. These rules follow the
[MySQL grant documentation](https://dev.mysql.com/doc/refman/8.0/en/grant.html).

Set `MYSQL_SSL_CA` to the trusted CA file for TLS with certificate and hostname
verification. Production configuration requires it. Optional mutual TLS uses
`MYSQL_SSL_CERT` and `MYSQL_SSL_KEY` together. Development without a CA uses a
non-TLS connection: use this only on a trusted local network. Paths refer to the
machine/container running the app.

## Run the API

```powershell
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Readiness checks connectivity and grants, and returns a generic failure reason
without database exception messages. Synchronous database calls run in a worker
thread. Application lifespan owns/disposes its pool; tests can inject a database.

## Test connectivity and extract metadata

```powershell
python scripts/check_db.py
python scripts/schema_extractor.py
```

The connectivity command returns exit code 0 only after verifying the authenticated
principal, explicit read-only grants, and SELECT connectivity. Extraction writes
`metadata/generated/schema_raw.json`. Override the destination with
`SCHEMA_OUTPUT_PATH` in `.env`, or use:

```powershell
python scripts/schema_extractor.py --output metadata/generated/schema_raw.json
```

Commands return exit code 1 on configuration, connection, permission, validation,
or output-write failures. They log the error category, never exception text,
credentials, SQL parameters, or customer rows. Existing output is preserved on
failed extraction; unchanged output is not rewritten. JSON is UTF-8 with stable
key/table/index/constraint ordering, while column and composite key/index order
remain their MySQL ordinal order. There are no timestamps or volatile row-count
estimates in the catalog, so repeated extraction of unchanged schema/server
metadata produces identical bytes.

The extractor reads SCHEMATA, TABLES, COLUMNS, TABLE_CONSTRAINTS, KEY_COLUMN_USAGE,
REFERENTIAL_CONSTRAINTS, STATISTICS, and CHECK_CONSTRAINTS where available, all
from INFORMATION_SCHEMA. It includes base tables/views; table/column comments;
column types, nullability, defaults, generated expressions, character sets and
precision; primary/unique/composite keys; FK targets and update/delete rules;
index type/order/prefix/visibility/expressions; and CHECK expressions/enforcement.
Capability fields explain which optional server metadata was available.

Every extraction uses a dedicated connection with a
[MySQL read-only transaction](https://dev.mysql.com/doc/refman/8.0/en/commit.html),
SELECT execution timeout, socket timeouts, and rollback on success or failure.
Only session-local settings are changed. No ERP DDL/DML, account changes, or
customer-data reads occur. Connection pooling uses SQLAlchemy's
[engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html),
with password-safe URL construction and parameter hiding.

The catalog validates duplicate/missing identifiers, column/key sequences,
key/constraint agreement, and FK columns/tables inside the configured database.
External-schema FK references are preserved as technical facts; extraction never
opens those schemas and their targets remain unvalidated. They must not later
become authorized joins automatically.

Accuracy limits: INFORMATION_SCHEMA defaults cannot distinguish an omitted
default from explicit DEFAULT NULL; both are represented as JSON null. Table
and column comments are technical facts, not verified business definitions.
Unrecognized table/constraint/index shapes fail closed. Unique functional indexes
without a column key definition currently fail validation instead of inventing
columns. Run extraction while DDL migrations are paused: separate metadata reads
do not provide an atomic schema snapshot. Detected inconsistencies abort the
operation; a read-only transaction alone cannot guarantee unchanged DDL.

## Catalog example

This abbreviated example is synthetic, not an assumed ERP schema. The complete
example is at `tests/fixtures/schema_raw.example.json`; real extraction fills it
with facts from your configured database.

```json
{
  "format_version": 1,
  "source": "mysql_information_schema",
  "schema_name": "fixture",
  "server_version": "8.0.fixture",
  "default_character_set": "utf8mb4",
  "default_collation": "utf8mb4_0900_ai_ci",
  "capabilities": {
    "index_expressions": true,
    "index_visibility": true,
    "check_constraints": true,
    "constraint_enforcement": true
  },
  "tables": [
    {
      "name": "child_records",
      "table_type": "BASE TABLE",
      "engine": "InnoDB",
      "comment": "Synthetic unit-test fixture",
      "columns": [
        {
          "name": "record_id",
          "ordinal_position": 1,
          "data_type": "int",
          "column_type": "int unsigned",
          "nullable": false,
          "default": null,
          "comment": ""
        }
      ],
      "primary_key": {"name": "PRIMARY", "columns": ["record_id", "revision"]},
      "foreign_keys": [
        {
          "name": "fk_parent",
          "columns": ["record_id", "revision"],
          "referenced_schema": "fixture",
          "referenced_table": "parent_records",
          "referenced_columns": ["record_id", "revision"],
          "referenced_unique_constraint": "PRIMARY",
          "referenced_unique_positions": [1, 2],
          "update_rule": "RESTRICT",
          "delete_rule": "RESTRICT",
          "match_option": "NONE"
        }
      ]
    }
  ]
}
```

The abbreviated snippet omits remaining columns, tables, indexes, unique keys,
and constraints; use the complete example for a valid catalog input.

## Checks

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Unit tests use synthetic INFORMATION_SCHEMA fixtures and require no MySQL or
secrets. They cover liveness/readiness, redaction, settings, pool URL encoding,
grants, transaction rollback, schema assembly, keys, capabilities, identifier
binding, malformed metadata, deterministic round trips, and atomic writes.

For an optional integration check, ask your DBA to load
`tests/fixtures/mysql_schema.sql` into a **disposable test database** and provision
its dedicated reader. Set the local connection variables to that test database,
then opt in explicitly (the test never performs setup DDL or writes):

```powershell
$env:RUN_MYSQL_INTEGRATION = '1'
python -m pytest tests/integration -q
Remove-Item Env:RUN_MYSQL_INTEGRATION
```

`RUN_MYSQL_INTEGRATION` must be set in the process environment for pytest to opt in;
the `.env` entry is just a reminder and does not enable test collection.

After extracting an existing application database, run the separate read-only
catalog integration check. It compares live metadata counts, verifies repeat
extraction produces identical JSON, and exercises `/health` and `/ready`. It
does not require the synthetic fixture tables or read customer records:

```powershell
$env:RUN_MYSQL_CATALOG_INTEGRATION = '1'
python -m pytest tests/integration/test_live_catalog.py -q
Remove-Item Env:RUN_MYSQL_CATALOG_INTEGRATION
```

The local reader account setup template is
`scripts/mysql_reader_setup.sql.example`. It is for a separate, authorized DBA
setup step only; the application and extractor never execute account-management
SQL. Once provisioned, keep only the reader credentials in `.env`.

## Docker

```powershell
docker compose build
docker compose up -d
```

Compose runs the API as a non-root container against your existing MySQL server,
binds the API to localhost, and mounts `metadata/generated` for extraction output.
It does not start or initialize MySQL. If MySQL runs on the Docker Desktop host,
use `MYSQL_HOST=host.docker.internal`. `/health` is the container liveness check;
deployment readiness should use `/ready`. For TLS, mount your CA/client files
read-only and set their container paths in `.env`.

```powershell
docker compose exec api python scripts/check_db.py
docker compose exec api python scripts/schema_extractor.py
```

Ensure the mounted generated directory is writable by the container's `agent`
user. The container root filesystem remains read-only.

## Folder structure and major files

```text
app/
  main.py                         # FastAPI app factory, /health, /ready
  config/settings.py              # Validated environment and secret settings
  db/mysql.py                     # Pool, grants, TLS, timeouts, read-only transactions
  observability/logging.py        # JSON logging with approved fields
  sql_agent/schema/
    models.py                     # Typed, validated raw schema catalog
    extractor.py                  # INFORMATION_SCHEMA readers and assembly
    catalog.py                    # Loading and atomic deterministic serialization
scripts/
  check_db.py
  schema_extractor.py
metadata/generated/
  .gitkeep                        # Real schema_raw.json appears after extraction
tests/
  unit/conftest.py                # Synthetic metadata and isolated configuration
  unit/test_foundation.py
  unit/test_schema.py
  integration/test_mysql_extraction.py
  integration/test_live_catalog.py
  fixtures/mysql_schema.sql       # Manual disposable test setup only
  fixtures/schema_raw.example.json
docker/Dockerfile
docker-compose.yml
.dockerignore
.env.example
.gitignore
pyproject.toml
requirements.txt
README.md
```

## Before the business metadata phase

Supply the generated raw catalog (not credentials), your MySQL version, and a
domain expert's verified explanations of legacy tables/columns. We will need:

- Business names, descriptions, domains, and company terminology/aliases.
- Status/enum code meanings and which values are valid.
- Date meanings: booking, journey, cancellation, accounting, and their timezones.
- Approved metrics/formulas, mandatory filters, currency and refund/tax handling.
- Logical relationships absent from physical FKs, including join columns/cardinality.
- Ambiguous concepts and verified defaults, if any.
- Approved report questions/queries and expected results using sanitized examples.
- Trusted backend company/branch/user authorization requirements and access scope.

No business meanings, enum mappings, join rules, or formulas will be inferred as
verified truth from names alone. Later enrichment must keep drafts separate from
expert-approved metadata.

## Edit and validate business metadata

Follow [metadata/REVIEW_GUIDE.md](metadata/REVIEW_GUIDE.md) for the exact manual
editing workflow. Table exclusions belong in `metadata/policies/table_scope.yaml`;
shared date/flag meanings belong in `metadata/columns/common_columns.yaml`;
table/column descriptions belong in `metadata/tables/<exact_table_name>.yaml`.
The glossary, metrics, logical relationships, and approved-query examples each
have their own YAML catalog. No metadata command changes ERP tables or records.

```powershell
python scripts/create_metadata_templates.py
python scripts/validate_metadata.py
```

The generator creates only missing files. The validator loads the extracted
schema locally and rejects unknown identifiers, unsafe YAML tags, duplicate
keys, incompatible date/flag types, and verified drafts without manual review
provenance. Production-facing catalog access requires reviewed tables and
columns, respects explicit exclusions, and requires both shared and local
approval for inherited definitions. Preferred table names never imply approval.

Current user-confirmed scope excludes `*seq`, legacy `driver` (an exact-name
pattern for future catalogs), and the 17 tables listed in
`metadata/policies/table_scope.yaml`, including `email_otp`, document tables,
role/screen/access tables, and `vehicle`. This leaves 21 tables eligible for
review; `tdriver` and `tvehicle` remain preferred. Existing
metadata files for excluded tables are retained for possible future inclusion,
including RBAC work; remove the exclusion and review the metadata when needed.
Shared flags, audit date meanings/format/timezone, creator/modifier references,
and the organisation reference were confirmed by the project owner on
2026-10-09. Table approval and each local column's review remain independent.

Metadata validation is not SQL validation. Formula parsing, query validation,
relationship-graph construction, retrieval, and execution remain later work.

The current column review covers eight tables and 189 approved columns:
`tdriver`, `tvehicle`, `trip`, `customer`, `tripinvoice`, `tripinvoiceitem`, and
`users`, plus `maintenance` (24 approved columns, with only the two branch fields
remaining drafts). Maintenance cost currency is INR, odometer readings are in
kilometres, and status/priority/type codes are confirmed. Its query example stays
unverified until SQL execution and expected-result checks pass.
Confirmed vocabulary is recorded in the glossary. The
[first report definitions](metadata/REPORT_REVIEW.md) now contain seven approved
primary metrics and six companion missing-value counts. The owner confirmed
active, non-cancelled source records, actual completion dates for trip reporting,
eligible invoice statuses, sums of known amounts with missing counts, and positive
outstanding balances only. Next are SQL examples and expected-result checks.
Query validation and execution are still later work.
