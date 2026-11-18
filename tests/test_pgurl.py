import pytest

from pgbackup_r2.errors import ConfigError
from pgbackup_r2.pgurl import child_env, pg_vars, redact, same_database


def test_url_to_pg_vars():
    env = pg_vars("postgresql://app:s3cret@db.example.com:6543/labpadi")
    assert env == {
        "PGHOST": "db.example.com",
        "PGPORT": "6543",
        "PGUSER": "app",
        "PGPASSWORD": "s3cret",
        "PGDATABASE": "labpadi",
    }


def test_percent_encoded_password_and_sslmode():
    env = pg_vars("postgres://app:p%40ss%3Aw%2Frd%25@db:5432/main?sslmode=require")
    assert env["PGPASSWORD"] == "p@ss:w/rd%"
    assert env["PGSSLMODE"] == "require"
    assert env["PGDATABASE"] == "main"


def test_defaults_are_left_to_libpq():
    assert pg_vars("postgresql:///localdb") == {"PGDATABASE": "localdb"}


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u:p@host/db",
        "http://host/db",
        "postgresql://u:p@host:5432/",
        "postgresql://u:p@host:5432",
        "postgresql://u:p@host:notaport/db",
        "just-a-string",
    ],
)
def test_rejects_bad_urls(url):
    with pytest.raises(ConfigError):
        pg_vars(url)


def test_bad_url_error_does_not_leak_password():
    with pytest.raises(ConfigError) as info:
        pg_vars("mysql://user:hunter2@host/db")
    assert "hunter2" not in str(info.value)


def test_child_env_strips_inherited_pg_vars():
    base = {"PATH": "/bin", "PGPASSWORD": "old", "PGSERVICE": "x", "PGOPTIONS": "-c y"}
    env = child_env("postgresql://u:new@h/db", base)
    assert env["PATH"] == "/bin"
    assert env["PGPASSWORD"] == "new"
    assert "PGSERVICE" not in env and "PGOPTIONS" not in env


def test_same_database_default_port_and_host():
    assert same_database("postgresql://a:x@db/app", "postgres://b:y@db:5432/app")
    assert same_database("postgresql:///app", "postgresql://u@localhost:5432/app")
    assert same_database("postgresql://u@127.0.0.1/app", "postgresql://u@localhost/app")
    assert not same_database("postgresql://db/app", "postgresql://db:5433/app")
    assert not same_database("postgresql://db/app", "postgresql://db/app_drill")
    assert not same_database("postgresql://db1/app", "postgresql://db2/app")


def test_redact():
    assert redact("postgres://user:hunter2@host/db") == "postgres://***@host/db"
    assert redact("postgresql://user:pw@host:5432/db?sslmode=require") == (
        "postgresql://***@host:5432/db?sslmode=require"
    )
    assert redact("postgresql://host/db?password=hunter2") == "postgresql://host/db?password=***"
    assert redact("postgresql://host/db") == "postgresql://host/db"
