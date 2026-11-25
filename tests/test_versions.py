"""Server and client major versions: a server newer than pg_dump fails early and plainly,
newer tools are fine for dumping, and a drill into an older scratch server warns."""

from __future__ import annotations

import logging

import boto3
import pytest

from pgbackup_r2 import commands, pg
from pgbackup_r2.cli import main
from pgbackup_r2.errors import CommandError

BUCKET = "versions"
RECIPIENT = "age1" + "q" * 58


@pytest.fixture
def clean(monkeypatch, tmp_path):
    for name in ("PGB_VERSION", "PGBACKUP_R2_IMAGE", "PG_MAJOR", "PGB_ENV_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "env, message, hint",
    [
        (
            {"PGB_VERSION": "0.4.3", "PGBACKUP_R2_IMAGE": "pg18"},
            "Your server is Postgres 19 but this image has pg_dump 18",
            "Set PG_MAJOR=19 in .env, so pgb uses the pg19 image",
        ),
        (
            {"PGBACKUP_R2_IMAGE": "pg18"},
            "Your server is Postgres 19 but this image has pg_dump 18",
            "Set PG_MAJOR=19 (pgb), or use the -pg19 image tag",
        ),
        (
            {},
            "Your server is Postgres 19 but pg_dump on this machine is 18",
            "Install postgresql-client-19 (macOS: brew install postgresql@19) and put its bin "
            "folder first on PATH",
        ),
    ],
)
def test_newer_server_says_the_fix_for_how_it_runs(clean, monkeypatch, env, message, hint):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(CommandError) as info:
        pg.check_can_dump(19, 18)
    assert (str(info.value), info.value.hint) == (message, hint)


def test_same_or_newer_client_can_dump(clean):
    pg.check_can_dump(18, 18)
    pg.check_can_dump(18, 19)  # a newer pg_dump dumps an older server fine
    pg.check_can_dump(19, None)  # unknown: let pg_dump decide


def test_backup_fails_before_dumping_a_newer_server(clean, monkeypatch, capsys):
    from moto import mock_aws

    monkeypatch.setenv("PGBACKUP_R2_IMAGE", "pg18")
    for k, v in {
        "DATABASE_URL": "postgresql://app:pw@db:5432/shop",
        "AGE_RECIPIENTS": RECIPIENT,
        "S3_BUCKET": BUCKET,
        "S3_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(commands, "require_tools", lambda *names: None)
    monkeypatch.setattr(pg, "server_version", lambda url: "19.0")
    monkeypatch.setattr(pg, "server_major", lambda url: 19)
    monkeypatch.setattr(pg, "client_major", lambda tool="pg_dump": 18)
    monkeypatch.setattr(pg, "table_counts", lambda url: pytest.fail("checked too late"))
    monkeypatch.setattr(pg, "dump_encrypted", lambda *a: pytest.fail("dumped anyway"))
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        assert main(["backup", "--plain"]) == 1
    err = capsys.readouterr().err
    assert (
        "ERROR Your server is Postgres 19 but this image has pg_dump 18 "
        "(Set PG_MAJOR=19 (pgb), or use the -pg19 image tag)" in err
    )


def test_doctor_fails_on_a_newer_server(clean, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost:5432/shop")
    monkeypatch.setattr(pg, "psql", lambda *a, **k: "190000")
    monkeypatch.setattr(pg, "server_version", lambda url: "19.0")
    monkeypatch.setattr(commands, "_tools_major", lambda: 18)
    assert main(["doctor", "--plain"]) == 1
    out = capsys.readouterr().out
    assert (
        "✗ Database: localhost/shop: Your server is Postgres 19 but pg_dump on this machine is 18"
        in out
    )
    assert "Install postgresql-client-19" in out


def test_dumped_by_major_reads_the_toc(monkeypatch):
    monkeypatch.setattr(
        pg,
        "list_toc",
        lambda path: ";\n; Dumped from database version: 18.6\n; Dumped by pg_dump version: 19.0\n",
    )
    assert pg.dumped_by_major("x.dump") == 19
    monkeypatch.setattr(pg, "list_toc", lambda path: "nothing useful")
    assert pg.dumped_by_major("x.dump") is None


@pytest.mark.parametrize("scratch, warned", [(18, True), (19, False), (20, False)])
def test_drill_warns_when_the_scratch_server_is_older(clean, monkeypatch, caplog, scratch, warned):
    monkeypatch.setattr(pg, "dumped_by_major", lambda path: 19)
    monkeypatch.setattr(pg, "server_major", lambda url: scratch)
    with caplog.at_level(logging.WARNING):
        commands._warn_older_scratch("postgresql://x@localhost/scratch", "x.dump")
    text = caplog.text
    if warned:
        assert (
            "this backup was made by pg_dump 19, but the scratch database is Postgres 18: "
            "newer dumps can carry settings older servers don't know" in text
        )
        assert "point DRILL_DATABASE_URL at a Postgres 19 server" in text
    else:
        assert text == ""


def test_drill_warning_under_pgb_names_pg_major(clean, monkeypatch, caplog):
    monkeypatch.setenv("PGB_VERSION", "0.4.3")
    monkeypatch.setattr(pg, "dumped_by_major", lambda path: 19)
    monkeypatch.setattr(pg, "server_major", lambda url: 18)
    with caplog.at_level(logging.WARNING):
        commands._warn_older_scratch("postgresql://x@localhost/scratch", "x.dump")
    assert "set PG_MAJOR=19 in .env" in caplog.text
