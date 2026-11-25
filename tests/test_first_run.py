"""First-run fixes for the CLI used directly (no pgb): hints, bucket and endpoint mistakes,
help that says what a command needs, and doctor's footgun checks. S3 is moto."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError

from pgbackup_r2 import commands, hints
from pgbackup_r2.cli import build_parser, main
from pgbackup_r2.config import ConfigError, Settings, endpoint_base, endpoint_path
from pgbackup_r2.errors import CommandError
from pgbackup_r2.stamps import manifest_key
from pgbackup_r2.storage import BucketError, Store

BUCKET = "first-run"
SETTINGS = (
    "S3_ENDPOINT",
    "S3_PREFIX",
    "TIERS",
    "PRUNE",
    "DATABASE_URL",
    "DRILL_DATABASE_URL",
    "AGE_RECIPIENTS",
    "AGE_IDENTITY",
    "AGE_IDENTITY_FILE",
    "PGB_VERSION",
    "PGB_ENV_KEYS",
)


@pytest.fixture
def s3(monkeypatch, tmp_path):
    from moto import mock_aws

    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env from the repo
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def put_backup(s3, key: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"age-encryption.org/v1 dump")
    s3.put_object(Bucket=BUCKET, Key=manifest_key(key), Body=b"age-encryption.org/v1 manifest")


def doctor(capsys) -> tuple[int, str]:
    code = main(["doctor", "--plain"])
    return code, capsys.readouterr().out


# --- 1. hints name the command that was run --------------------------------------------------


def test_hints_name_pgb_only_under_the_wrapper(monkeypatch):
    monkeypatch.delenv("PGB_VERSION", raising=False)
    assert hints.cmd("drill") == "`pgbackup-r2 drill`"
    assert hints.doctor().startswith("Run `pgbackup-r2 doctor`")
    assert build_parser().prog == "pgbackup-r2"
    monkeypatch.setenv("PGB_VERSION", "0.4.2")
    assert hints.cmd("drill") == "`pgb drill`"
    assert hints.doctor().startswith("Run `pgb doctor`")
    assert build_parser().prog == "pgb"


def test_no_source_file_hard_codes_a_pgb_command():
    """Every message that names a command goes through hints.cmd(). Docstrings don't count."""
    import ast
    import pathlib
    import re

    pattern = re.compile(r"`pgb (?:doctor|drill|list|backup|prune|keygen|init)")
    found = []
    for path in pathlib.Path(commands.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        docs = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        found += [
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
            and pattern.search(node.value)
        ]
    assert found == []


def test_wrong_backup_hint_follows_the_command(s3, monkeypatch, capsys):
    monkeypatch.setenv("AGE_IDENTITY", "AGE-SECRET-KEY-1" + "Q" * 58)
    put_backup(s3, "2026-11-18T020000Z.dump.age")
    assert main(["drill", "--preflight", "--plain", "2026-11-19T020000Z"]) == 1
    assert "Run `pgbackup-r2 list` to see the backups" in capsys.readouterr().err
    monkeypatch.setenv("PGB_VERSION", "0.4.2")
    assert main(["drill", "--preflight", "--plain", "2026-11-19T020000Z"]) == 1
    assert "Run `pgb list` to see the backups" in capsys.readouterr().err


# --- 2. an empty or wrong bucket ------------------------------------------------------------


def test_empty_and_unset_bucket_are_told_apart():
    with pytest.raises(ConfigError, match="^S3_BUCKET is empty$"):
        Settings.from_env({"S3_BUCKET": ""})
    with pytest.raises(ConfigError, match="^S3_BUCKET is empty$"):
        Settings.from_env({"S3_BUCKET": "   "})
    with pytest.raises(ConfigError, match="^S3_BUCKET is not set$"):
        Settings.from_env({})


def test_bucket_with_a_folder_says_where_the_folder_goes():
    with pytest.raises(ConfigError, match=r"S3_BUCKET has a folder in it") as info:
        Settings.from_env({"S3_BUCKET": "iotabyt-backups/labpadi"})
    assert info.value.hint == (
        "S3_BUCKET is just the name: S3_BUCKET=iotabyt-backups, and the folder goes in "
        "S3_PREFIX=labpadi"
    )


def test_empty_bucket_fails_before_any_s3_call(monkeypatch, capsys):
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setattr(
        "pgbackup_r2.storage.make_client", lambda s: pytest.fail("no S3 call expected")
    )
    assert main(["list", "--plain"]) == 1
    assert "ERROR S3_BUCKET is empty" in capsys.readouterr().err


def test_a_bucket_that_isnt_there_names_the_settings(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_BUCKET", "no-such-bucket")
    assert main(["list", "--plain"]) == 1
    err = capsys.readouterr().err
    assert "can't list s3://no-such-bucket/: AWS S3 says NoSuchBucket" in err
    assert "Check S3_BUCKET is the bucket's exact name, and S3_ENDPOINT" in err
    assert "botocore" not in err and "ListObjectsV2" not in err


class _NoSuchKeyPaginator:
    def paginate(self, **kwargs):
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "ListObjectsV2")


class _Client:
    def get_paginator(self, name):
        return _NoSuchKeyPaginator()


def test_nosuchkey_from_a_list_call_is_about_the_endpoint():
    """What R2 answers when the bucket name ended up in S3_ENDPOINT's path."""
    s = Settings.from_env(
        {"S3_BUCKET": "labpadi", "S3_ENDPOINT": "https://acc.r2.cloudflarestorage.com/iotabyt"}
    )
    with pytest.raises(BucketError) as info:
        Store(s, client=_Client()).list_backups()
    assert str(info.value) == "can't list s3://labpadi/: S3_ENDPOINT has a path (/iotabyt)"
    assert info.value.hint == (
        "Drop the path; the bucket goes in S3_BUCKET: "
        "S3_ENDPOINT=https://acc.r2.cloudflarestorage.com"
    )

    s = Settings.from_env({"S3_BUCKET": "labpadi", "S3_ENDPOINT": "https://acc.example.com"})
    with pytest.raises(BucketError, match="https://acc.example.com says NoSuchKey"):
        Store(s, client=_Client()).resolve("latest")


def test_endpoint_path_helpers():
    assert endpoint_path(None) is None
    assert endpoint_path("https://a.r2.cloudflarestorage.com") is None
    assert endpoint_path("https://a.r2.cloudflarestorage.com/") is None
    assert endpoint_path("https://a.r2.cloudflarestorage.com/iotabyt-backups") == "/iotabyt-backups"
    assert endpoint_base("https://a.r2.cloudflarestorage.com/x/y") == (
        "https://a.r2.cloudflarestorage.com"
    )


# --- 3. help says what a command needs ------------------------------------------------------


@pytest.mark.parametrize(
    "command, needs",
    [
        ("drill", ["DRILL_DATABASE_URL", "may wipe", "AGE_IDENTITY", "AGE_IDENTITY_FILE"]),
        ("restore", ["RESTORE_DATABASE_URL", "--target", "AGE_IDENTITY", "AGE_IDENTITY_FILE"]),
        ("verify", ["AGE_IDENTITY", "AGE_IDENTITY_FILE", "not with --file"]),
        ("decrypt", ["AGE_IDENTITY", "AGE_IDENTITY_FILE", "not with --file"]),
    ],
)
def test_help_says_what_the_command_needs(command, needs, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    with pytest.raises(SystemExit):
        main([command, "--help"])
    out = capsys.readouterr().out
    assert "Needs:" in out
    for word in needs:
        assert word in out


def test_main_help_says_env_vars_and_no_dotenv(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "it doesn't read .env itself" in out
    assert "set -a; source .env; set +a" in out


# --- 4 and 5. doctor: a list call, and two footguns -----------------------------------------


def test_doctor_lists_the_bucket(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_PREFIX", "shop")
    put_backup(s3, "shop/2026-11-18T020000Z.dump.age")
    _, out = doctor(capsys)
    assert "✓ Bucket: listed s3://first-run/shop/: 1 backup\n" in out
    assert "✓ Bucket: s3://first-run/shop/ is writable" in out


def test_doctor_stops_at_a_bucket_it_cant_list(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_BUCKET", "no-such-bucket")
    code, out = doctor(capsys)
    assert code == 1
    assert "✗ Bucket: can't list s3://no-such-bucket/: AWS S3 says NoSuchBucket" in out
    assert "is writable" not in out and "can't write" not in out


def test_doctor_fails_on_an_endpoint_with_a_path(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_ENDPOINT", "https://acc.r2.cloudflarestorage.com/iotabyt-backups")
    code, out = doctor(capsys)
    assert code == 1
    assert "✗ Settings: S3_ENDPOINT has a path (/iotabyt-backups)" in out
    assert (
        "Drop the path; the bucket goes in S3_BUCKET: "
        "S3_ENDPOINT=https://acc.r2.cloudflarestorage.com" in out
    )
    assert "Bucket:" not in out  # every S3 call would fail the same way


def test_doctor_fails_when_drill_and_live_database_are_the_same(s3, monkeypatch, capsys):
    monkeypatch.setattr(commands.pg, "psql", lambda *a, **k: "170002")
    monkeypatch.setattr(commands.pg, "server_version", lambda url: "17.2")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost:5432/postgres")
    monkeypatch.setenv("DRILL_DATABASE_URL", "postgresql://other:pw2@localhost/postgres")
    code, out = doctor(capsys)
    assert code == 1
    assert (
        "✗ Settings: DRILL_DATABASE_URL and DATABASE_URL point at the same database "
        "(localhost/postgres)" in out
    )

    monkeypatch.setenv("DRILL_DATABASE_URL", "postgresql://app:pw@localhost:5432/scratch")
    _, out = doctor(capsys)
    assert "point at the same database" not in out


def test_doctor_says_empty_rather_than_not_set(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setenv("AGE_RECIPIENTS", "")
    _, out = doctor(capsys)
    assert "✗ Settings: DATABASE_URL is not set; S3_BUCKET, AGE_RECIPIENTS are empty" in out
    assert "Every setting: https://pgbackup-r2.iotabyt.com/reference/environment/" in out
    assert "pgb init" not in out


# --- 6. backups in tier folders, with TIERS unset -------------------------------------------


@pytest.fixture
def tiered(s3, monkeypatch):
    monkeypatch.setenv("S3_PREFIX", "labpadi")
    put_backup(s3, "labpadi/daily/2026-11-18T020000Z.dump.age")
    put_backup(s3, "labpadi/monthly/2026-11-01T020000Z.dump.age")
    return s3


def test_resolve_names_the_folders_and_the_tiers_line(tiered, monkeypatch, capsys):
    monkeypatch.setenv("AGE_IDENTITY", "AGE-SECRET-KEY-1" + "Q" * 58)
    assert main(["drill", "--preflight", "--plain"]) == 1
    err = capsys.readouterr().err
    assert (
        "no backups found under s3://first-run/labpadi/, but found backups in "
        "labpadi/daily/ and labpadi/monthly/" in err
    )
    assert 'Set TIERS (e.g. TIERS="daily=daily/ monthly=monthly/")' in err


def test_list_names_the_folders_and_the_tiers_line(tiered, capsys):
    assert main(["list", "--plain"]) == 0
    captured = capsys.readouterr()
    assert "found backups in labpadi/daily/ and labpadi/monthly/" in captured.err
    assert captured.out.strip() == 'Set TIERS (e.g. TIERS="daily=daily/ monthly=monthly/")'


def test_doctor_warns_about_backups_in_unused_folders(tiered, capsys):
    _, out = doctor(capsys)
    assert (
        "⚠ TIERS isn't set and there are no backups directly under s3://first-run/labpadi/, "
        "but found backups in labpadi/daily/ and labpadi/monthly/." in out
    )


def test_with_tiers_set_the_folders_are_used(tiered, monkeypatch, capsys):
    monkeypatch.setenv("TIERS", "daily=daily/ monthly=monthly/")
    assert main(["list", "--plain"]) == 0
    assert "Set TIERS" not in capsys.readouterr().out


# --- 7. .env is never read by the CLI -------------------------------------------------------


def test_doctor_says_a_dotenv_here_isnt_read(s3, tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("# comment\nS3_BUCKET=first-run\nexport TIERS='x'\nRANDOM=1\n")
    _, out = doctor(capsys)
    assert (
        "⚠ .env: pgbackup-r2 doesn't read .env, and TIERS, RANDOM from it aren't in the "
        "environment." in out
    )
    assert "set -a; source .env; set +a" in out

    monkeypatch.setenv("TIERS", "")
    monkeypatch.setenv("RANDOM", "1")
    _, out = doctor(capsys)
    assert "- .env: pgbackup-r2 doesn't read .env itself; its settings are in the environment" in (
        out
    )

    monkeypatch.setenv("PGB_VERSION", "0.4.2")  # pgb hands .env to Docker: nothing to say
    _, out = doctor(capsys)
    assert ".env:" not in out


# --- 8. drill refuses the live database, naming both ----------------------------------------


def test_drill_refusal_names_both_settings():
    s = Settings.from_env(
        {
            "S3_BUCKET": BUCKET,
            "DATABASE_URL": "postgresql://app:pw@localhost:5432/postgres",
            "DRILL_DATABASE_URL": "postgresql://app:pw@localhost/postgres",
        }
    )
    with pytest.raises(CommandError) as info:
        commands.drill(s, "latest")
    assert str(info.value) == (
        "DRILL_DATABASE_URL and DATABASE_URL point at the same database (localhost/postgres)"
    )
    assert (
        info.value.hint == "A drill wipes its database: point DRILL_DATABASE_URL at a scratch one"
    )


# --- found on a real first run ----------------------------------------------------------------


def test_upload_to_a_missing_bucket_is_one_line_not_a_traceback(s3, tmp_path):
    s = Settings.from_env(
        {
            "S3_BUCKET": "no-such-bucket",
            "S3_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
        }
    )
    dump = tmp_path / "x.dump.age"
    dump.write_bytes(b"age-encryption.org/v1 dump")
    with pytest.raises(BucketError) as info:
        Store(s).upload_file(str(dump), "x.dump.age", {"sha256": "x"})
    assert str(info.value).startswith(
        "can't upload to s3://no-such-bucket/: AWS S3 says NoSuchBucket"
    )


def test_doctor_without_pgb_is_fine_with_newer_tools_but_warns(s3, monkeypatch, capsys):
    monkeypatch.delenv("PGBACKUP_R2_IMAGE", raising=False)
    monkeypatch.setattr(commands.pg, "psql", lambda *a, **k: "180006")
    monkeypatch.setattr(commands.pg, "server_version", lambda url: "18.6")
    monkeypatch.setattr(commands, "_tools_major", lambda: 19)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost:5432/shop")
    _, out = doctor(capsys)
    assert (
        "⚠ Database: connected to localhost/shop, PostgreSQL 18.6, and pg_dump here is 19: "
        "fine for backups." in out
    )
    assert "✗ Database" not in out
    assert "PG_MAJOR" not in out  # never set, so never blamed

    monkeypatch.setenv("PGB_VERSION", "0.4.3")  # under pgb: unchanged
    _, out = doctor(capsys)
    assert "✗ Database: localhost/shop is PostgreSQL 18.6, but PG_MAJOR is 19" in out


def test_doctor_doesnt_say_all_set_when_the_endpoint_has_a_path(s3, monkeypatch, capsys):
    for name, value in (
        ("DATABASE_URL", "postgresql://app:pw@localhost:5432/shop"),
        ("AGE_RECIPIENTS", "age1" + "q" * 58),
        ("S3_ENDPOINT", "https://acc.r2.cloudflarestorage.com/iotabyt-backups"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(commands.pg, "psql", lambda *a, **k: "170002")
    monkeypatch.setattr(commands.pg, "server_version", lambda url: "17.2")
    _, out = doctor(capsys)
    assert "everything a backup needs is set" not in out
    assert "✗ Settings: S3_ENDPOINT has a path (/iotabyt-backups)" in out
