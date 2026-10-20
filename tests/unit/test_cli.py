from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config.settings import Settings
from scripts import check_db, schema_extractor


def test_cli_failure_is_redacted_and_preserves_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "schema_raw.json"
    output.write_text("previous snapshot", encoding="utf-8")
    config = Settings(_env_file=None, schema_output_path=output)
    db = MagicMock()
    db.read_only_connection.side_effect = RuntimeError("secret-password")
    monkeypatch.setattr(schema_extractor, "Settings", lambda: config)
    monkeypatch.setattr(schema_extractor, "MySQLDatabase", lambda _: db)
    monkeypatch.setattr("sys.argv", ["schema_extractor.py"])
    assert schema_extractor.main() == 1
    assert output.read_text() == "previous snapshot"
    assert not list(tmp_path.glob("*.tmp"))
    db.dispose.assert_called_once()
    assert "secret-password" not in capsys.readouterr().out


def test_connectivity_cli_returns_failure_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check_db, "Settings", lambda: Settings(_env_file=None))
    assert check_db.main() == 1
