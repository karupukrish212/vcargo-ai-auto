import json
import os
import tempfile
from pathlib import Path

from app.sql_agent.schema.models import RawSchemaCatalog


def canonical_json(catalog: RawSchemaCatalog) -> str:
    """Stable UTF-8 representation without timestamps, statistics, or customer data."""
    data = catalog.model_dump(mode="json")
    data["tables"].sort(key=lambda table: table["name"])
    for table in data["tables"]:
        table["columns"].sort(key=lambda column: column["ordinal_position"])
        for collection in ("indexes", "foreign_keys", "unique_keys", "constraints"):
            table[collection].sort(key=lambda item: item["name"])
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def load_catalog(path: Path) -> RawSchemaCatalog:
    return RawSchemaCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def write_catalog(catalog: RawSchemaCatalog, path: Path) -> bool:
    """Atomically replace only a complete validated catalog; unchanged content is a no-op."""
    payload = canonical_json(RawSchemaCatalog.model_validate(catalog.model_dump())).encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True
