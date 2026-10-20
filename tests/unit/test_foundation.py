import json
import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config.settings import Settings
from app.db.mysql import DatabaseSafetyError, MySQLDatabase, validate_read_only_grants
from app.main import create_app
from app.observability.logging import JsonFormatter


def settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_liveness_does_not_require_database() -> None:
    with TestClient(create_app(settings())) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503


def test_readiness_checks_injected_database_without_disposing_it() -> None:
    db = MagicMock()
    with TestClient(create_app(settings(), db)) as client:
        assert client.get("/ready").json() == {"status": "ready"}
        db.check_connectivity.side_effect = RuntimeError("secret-password")
        response = client.get("/ready")
        assert response.status_code == 503
        assert "secret-password" not in response.text
    db.dispose.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        {"mysql_user": "root"},
        {"mysql_database": "mysql"},
        {"mysql_port": 0},
        {"mysql_ssl_cert": "client.pem"},
        {"mysql_connect_timeout_seconds": -1},
        {
            "app_env": "production",
            "mysql_host": "db",
            "mysql_database": "erp",
            "mysql_user": "reader",
            "mysql_password": "secret",
        },
    ],
)
def test_unsafe_settings_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        settings(**values)


def test_blank_tls_environment_paths_are_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYSQL_SSL_CA", "")
    assert settings().mysql_ssl_ca is None


def test_explicit_read_only_grants() -> None:
    validate_read_only_grants(
        [
            "GRANT USAGE ON *.* TO `reader`@`%`",
            "GRANT SELECT, SHOW VIEW ON `erp`.* TO `reader`@`%`",
        ],
        "erp",
    )


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT ALL PRIVILEGES ON `erp`.* TO `reader`@`%`",
        "GRANT SELECT, INSERT ON `erp`.* TO `reader`@`%`",
        "GRANT SELECT ON *.* TO `reader`@`%`",
        "GRANT SELECT ON `other`.* TO `reader`@`%`",
        "GRANT SELECT ON `erp`.* TO `reader`@`%` WITH GRANT OPTION",
        "GRANT `readonly_role`@`%` TO `reader`@`%`",
        "GRANT SELECT ON `erp`.`one_table` TO `reader`@`%`",
    ],
)
def test_unsafe_or_incomplete_grants_rejected(grant: str) -> None:
    with pytest.raises(DatabaseSafetyError):
        validate_read_only_grants([grant], "erp")


def test_incomplete_database_configuration_rejected() -> None:
    with pytest.raises(DatabaseSafetyError):
        MySQLDatabase(settings())


def test_database_url_handles_special_characters() -> None:
    config = settings(
        mysql_host="db", mysql_database="erp", mysql_user="reader", mysql_password="p@ss:/?#"
    )
    db = MySQLDatabase(config)
    assert db.engine.url.password == "p@ss:/?#"
    assert "p@ss" not in repr(config)
    db.dispose()


def test_log_formatter_ignores_exception_and_unapproved_fields() -> None:
    record = logging.LogRecord("app.test", logging.ERROR, "", 0, "check_failed", (), None)
    record.password = "secret"
    record.error_type = "OperationalError"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "check_failed"
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize("fail_in_body", [False, True])
def test_read_only_transaction_always_rolls_back(fail_in_body: bool) -> None:
    config = settings(
        mysql_host="db", mysql_database="erp", mysql_user="reader", mysql_password="secret"
    )
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "reader@localhost"
    connection.exec_driver_sql.side_effect = [
        None,
        None,
        [("partial_revokes", "OFF")],
        [
            ("GRANT SELECT ON `erp`.* TO `reader`@`localhost`",),
        ],
    ]
    db = MySQLDatabase(config, engine)
    if fail_in_body:
        with pytest.raises(RuntimeError), db.read_only_connection():
            raise RuntimeError("failed")
    else:
        with db.read_only_connection() as checked:
            assert checked is connection
    commands = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert commands[:2] == ["SET SESSION MAX_EXECUTION_TIME = 5000", "START TRANSACTION READ ONLY"]
    connection.rollback.assert_called_once()


def test_validation_errors_do_not_expose_passwords() -> None:
    with pytest.raises(ValidationError) as error:
        settings(mysql_user="root", mysql_password="must-never-appear")
    assert "must-never-appear" not in str(error.value)


def test_wildcard_characters_in_database_grants_must_be_literal() -> None:
    with pytest.raises(DatabaseSafetyError):
        validate_read_only_grants(["GRANT SELECT ON `erp_test`.* TO `reader`@`%`"], "erp_test")
    validate_read_only_grants([r"GRANT SELECT ON `erp\_test`.* TO `reader`@`%`"], "erp_test")
    validate_read_only_grants(
        ["GRANT SELECT ON `erp_test`.* TO `reader`@`%`"], "erp_test", partial_revokes=True
    )


def test_write_privileges_prevent_connection_from_being_exposed() -> None:
    config = settings(
        mysql_host="db", mysql_database="erp", mysql_user="reader", mysql_password="secret"
    )
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "reader@localhost"
    connection.exec_driver_sql.side_effect = [
        None,
        None,
        [],
        [
            ("GRANT SELECT, UPDATE ON `erp`.* TO `reader`@`localhost`",),
        ],
    ]
    db = MySQLDatabase(config, engine)
    entered = False
    with pytest.raises(DatabaseSafetyError), db.read_only_connection():
        entered = True
    assert not entered
    connection.rollback.assert_called_once()
