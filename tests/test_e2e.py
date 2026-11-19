"""End-to-end tests against a real Postgres, real age, and S3 mocked by moto.

Skipped unless PGBACKUP_TEST_URL points at a Postgres you can create databases on, e.g.

    docker run -d -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
    PGBACKUP_TEST_URL=postgresql://postgres:pw@localhost:5432/postgres uv run pytest

The pg tools on PATH must match the server's major version.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import boto3
import pytest

from pgbackup_r2 import pg
from pgbackup_r2.cli import main
from pgbackup_r2.pgurl import child_env

ADMIN_URL = os.environ.get("PGBACKUP_TEST_URL")
TOOLS = ("pg_dump", "pg_restore", "psql", "age", "age-keygen")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or any(shutil.which(t) is None for t in TOOLS),
    reason="needs PGBACKUP_TEST_URL and pg_dump, pg_restore, psql, age, age-keygen on PATH",
)

BUCKET = "pgbackup-test"
PREFIX = "testapp"
MARKER = "plaintext-marker-school"
EXPECTED = {'public."Readings"': 5000, "public.schools": 250}
SEED_SQL = f"""
CREATE TABLE schools (id serial PRIMARY KEY, name text NOT NULL);
INSERT INTO schools (name) SELECT '{MARKER}-' || g FROM generate_series(1, 250) g;
CREATE TABLE "Readings" (
    id serial PRIMARY KEY,
    school_id int NOT NULL REFERENCES schools (id),
    value numeric NOT NULL
);
INSERT INTO "Readings" (school_id, value)
SELECT 1 + (g % 250), g * 0.25 FROM generate_series(1, 5000) g;
"""


def with_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname, parts.query, ""))


def admin_sql(sql: str) -> None:
    subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", sql],
        env=child_env(ADMIN_URL),
        check=True,
        capture_output=True,
    )


def sql(url: str, text: str) -> None:
    subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", text],
        env=child_env(url),
        check=True,
        capture_output=True,
    )


def make_keypair() -> tuple[str, str]:
    out = subprocess.run(["age-keygen"], check=True, capture_output=True, text=True).stdout
    secret = next(ln for ln in out.splitlines() if ln.startswith("AGE-SECRET-KEY-1"))
    public = subprocess.run(
        ["age-keygen", "-y"], input=secret + "\n", check=True, capture_output=True, text=True
    ).stdout.strip()
    return public, secret


@pytest.fixture
def databases():
    suffix = uuid.uuid4().hex[:8]
    live, scratch = f"pgbr_live_{suffix}", f"pgbr_scratch_{suffix}"
    admin_sql(f"CREATE DATABASE {live}")
    admin_sql(f"CREATE DATABASE {scratch}")
    live_url, scratch_url = with_db(ADMIN_URL, live), with_db(ADMIN_URL, scratch)
    sql(live_url, SEED_SQL)
    try:
        yield live_url, scratch_url
    finally:
        admin_sql(f"DROP DATABASE IF EXISTS {live} WITH (FORCE)")
        admin_sql(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")


@pytest.fixture
def keys(tmp_path):
    public, secret = make_keypair()
    key_file = tmp_path / "key.txt"
    key_file.write_text(secret + "\n")
    key_file.chmod(0o600)
    return public, secret, str(key_file)


@pytest.fixture
def s3():
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def env(monkeypatch, databases, keys, s3):
    live_url, scratch_url = databases
    public, _secret, key_file = keys
    for name in (
        "S3_ENDPOINT",
        "AGE_IDENTITY",
        "RESTORE_DATABASE_URL",
        "HEARTBEAT_URL",
        "HEARTBEAT_FAIL_URL",
        "RETENTION_DAYS",
        "MIN_KEEP",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", live_url)
    monkeypatch.setenv("DRILL_DATABASE_URL", scratch_url)
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_PREFIX", PREFIX)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AGE_RECIPIENTS", public)
    monkeypatch.setenv("AGE_IDENTITY_FILE", key_file)
    return {"live": live_url, "scratch": scratch_url, "s3": s3, "keys": keys}


def objects(s3) -> list[str]:
    return [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]


def dump_object(s3) -> str:
    return next(k for k in objects(s3) if k.endswith(".dump.age"))


def test_backup_then_drill_passes(env, capsys):
    assert main(["backup"]) == 0
    keys = objects(env["s3"])
    assert len(keys) == 2
    dump_key = dump_object(env["s3"])
    assert re.fullmatch(rf"{PREFIX}/\d{{4}}-\d\d-\d\dT\d{{6}}Z\.dump\.age", dump_key)
    assert dump_key.replace(".dump.age", ".manifest.age") in keys

    obj = env["s3"].get_object(Bucket=BUCKET, Key=dump_key)
    body = obj["Body"].read()
    assert body.startswith(b"age-encryption.org/v1")
    assert MARKER.encode() not in body
    assert set(obj["Metadata"]) >= {"sha256", "pg-version", "tool-version"}
    manifest = env["s3"].get_object(Bucket=BUCKET, Key=keys[1] if keys[0] == dump_key else keys[0])
    assert manifest["Body"].read().startswith(b"age-encryption.org/v1")

    capsys.readouterr()
    assert main(["drill"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"PASS  {dump_key}")
    assert "2 tables and 5,250 rows match the manifest" in out
    assert pg.table_counts(env["scratch"]) == EXPECTED

    assert main(["list"]) == 0
    assert dump_key in capsys.readouterr().out


def test_drill_fails_on_extra_table(env, capsys):
    assert main(["backup"]) == 0
    sql(env["scratch"], "CREATE TABLE leftover (id int); INSERT INTO leftover VALUES (1)")
    capsys.readouterr()
    assert main(["drill", "latest"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL  ")
    assert "  extra table public.leftover" in out


def test_age_identity_string_works_and_key_file_is_removed(env, monkeypatch, tmp_path, capsys):
    _public, secret, _file = env["keys"]
    monkeypatch.delenv("AGE_IDENTITY_FILE")
    monkeypatch.setenv("AGE_IDENTITY", secret)
    private_tmp = tmp_path / "tmp"
    private_tmp.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(private_tmp))

    assert main(["backup"]) == 0
    assert main(["drill"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert list(private_tmp.iterdir()) == []


def test_decrypt(env, tmp_path, monkeypatch, caplog, capsys):
    assert main(["backup"]) == 0
    out = tmp_path / "backup.dump"
    assert main(["decrypt", "latest", "--out", str(out)]) == 0
    assert out.read_bytes().startswith(b"PGDMP")
    assert "pg_restore --no-owner --no-acl -d <db>" in capsys.readouterr().out

    # refuses to overwrite without --force
    assert main(["decrypt", "--out", str(out)]) == 1
    assert "already exists" in caplog.text
    assert main(["decrypt", "--out", str(out), "--force"]) == 0

    # --file needs no S3 settings at all
    local = tmp_path / "copy.dump.age"
    env["s3"].download_file(BUCKET, dump_object(env["s3"]), str(local))
    for name in ("S3_BUCKET", "S3_PREFIX", "S3_REGION", "DATABASE_URL", "DRILL_DATABASE_URL"):
        monkeypatch.delenv(name)
    out2 = tmp_path / "from-file.dump"
    assert main(["decrypt", "--file", str(local), "--out", str(out2)]) == 0
    assert out2.read_bytes().startswith(b"PGDMP")

    # the wrong key fails with a plain message and leaves nothing behind
    _other_public, other_secret = make_keypair()
    monkeypatch.delenv("AGE_IDENTITY_FILE")
    monkeypatch.setenv("AGE_IDENTITY", other_secret)
    caplog.clear()
    out3 = tmp_path / "wrong.dump"
    assert main(["decrypt", "--file", str(local), "--out", str(out3)]) == 1
    assert "private key doesn't match this backup" in caplog.text
    assert "Traceback" not in caplog.text
    assert not out3.exists() and not (tmp_path / "wrong.dump.partial").exists()


def test_verify_lists_tables_without_a_database(env, monkeypatch, capsys):
    assert main(["backup"]) == 0
    capsys.readouterr()
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("DRILL_DATABASE_URL")
    for name in [k for k in os.environ if k.startswith("PG")]:
        monkeypatch.delenv(name)
    assert main(["verify"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK  ")
    assert re.search(r'public\."Readings"\s+5,000', out)
    assert re.search(r"public\.schools\s+250", out)


def test_keygen(capsys):
    assert main(["keygen"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Your new key pair. Do this once.\n")
    public = re.search(r"^AGE_RECIPIENTS=(age1[0-9a-z]{58})$", out, re.MULTILINE)
    secret = re.search(r"^AGE_IDENTITY=(AGE-SECRET-KEY-1[0-9A-Z]{58})$", out, re.MULTILINE)
    assert public and secret
    assert public.start() < secret.start()
    derived = subprocess.run(
        ["age-keygen", "-y"], input=secret.group(1) + "\n", capture_output=True, text=True
    ).stdout.strip()
    assert derived == public.group(1)


def test_restore_refuses_live_database(env, caplog):
    assert main(["backup"]) == 0
    assert main(["restore", "latest", "--target", env["live"]]) == 1
    assert "refusing to restore" in caplog.text
    # same database written differently is still refused
    parts = urlsplit(env["live"])
    if parts.hostname == "localhost":
        alt = env["live"].replace("@localhost", "@127.0.0.1")
        assert main(["restore", "--target", alt]) == 1


def test_backup_refuses_without_recipients(env, monkeypatch, caplog):
    monkeypatch.delenv("AGE_RECIPIENTS")
    assert main(["backup"]) == 1
    assert "AGE_RECIPIENTS is empty" in caplog.text
    assert objects(env["s3"]) == []


ANSI = "\x1b["


def test_plain_mode_has_no_ansi_and_no_color_disables_colour(env, capsys, monkeypatch):
    # Not a terminal (as in tests, containers, cron): plain.
    assert main(["backup"]) == 0
    assert main(["list"]) == 0
    assert main(["drill"]) == 0
    captured = capsys.readouterr()
    assert ANSI not in captured.out and ANSI not in captured.err
    assert "PASS  " in captured.out

    # Pretend to be a terminal: interactive mode really does use colour...
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert main(["list"]) == 0
    assert ANSI in capsys.readouterr().out

    # ...but --plain and NO_COLOR=1 turn it off.
    assert main(["--plain", "list"]) == 0
    assert main(["drill", "--plain"]) == 0
    monkeypatch.setenv("NO_COLOR", "1")
    assert main(["list"]) == 0
    assert main(["drill"]) == 0
    captured = capsys.readouterr()
    assert ANSI not in captured.out and ANSI not in captured.err
    assert captured.out.count("PASS  ") == 2


def test_restore_target_from_env(env, monkeypatch, caplog):
    assert main(["backup"]) == 0

    # Neither --target nor RESTORE_DATABASE_URL: a plain config error.
    assert main(["restore"]) == 1
    assert "no restore target" in caplog.text

    # RESTORE_DATABASE_URL pointing at the live database is refused like --target.
    caplog.clear()
    monkeypatch.setenv("RESTORE_DATABASE_URL", env["live"])
    assert main(["restore", "latest"]) == 1
    assert "refusing to restore" in caplog.text

    # Pointing at another database, the URL never has to be on the command line.
    monkeypatch.setenv("RESTORE_DATABASE_URL", env["scratch"])
    assert main(["restore", "latest"]) == 0
    assert pg.table_counts(env["scratch"]) == EXPECTED

    # --target still works, and wins over the env var.
    monkeypatch.setenv("RESTORE_DATABASE_URL", env["live"])
    assert main(["restore", "--target", env["scratch"]]) == 0


def server_major(url: str) -> int:
    return int(pg.psql(url, "SHOW server_version_num")) // 10000


def test_doctor_all_good_and_leaves_nothing_in_the_bucket(env, monkeypatch, capsys):
    monkeypatch.setenv("PG_MAJOR", str(server_major(env["live"])))
    assert main(["doctor", "--plain"]) == 0
    out = capsys.readouterr().out
    assert "✓ Settings: everything a backup needs is set (storage: AWS S3)" in out
    assert "✓ Database: connected to localhost/pgbr_live_" in out
    assert f"✓ Bucket: s3://{BUCKET}/{PREFIX}/ is writable" in out
    assert "✓ Keys: AGE_IDENTITY opens what is encrypted to AGE_RECIPIENTS" in out
    assert "✗" not in out
    assert out.rstrip().endswith("All checks passed.")
    assert objects(env["s3"]) == []


def test_doctor_reports_each_problem_with_a_fix(env, monkeypatch, capsys):
    major = server_major(env["live"])
    other_public, _ = make_keypair()
    own_public = env["keys"][0]
    monkeypatch.setenv("PG_MAJOR", str(major + 1))
    monkeypatch.setenv("AGE_RECIPIENTS", other_public)
    assert main(["doctor", "--plain"]) == 1
    out = capsys.readouterr().out
    assert f"but PG_MAJOR is {major + 1}" in out
    assert f"Set PG_MAJOR={major} in .env" in out
    assert "✗ Keys: AGE_IDENTITY can't open backups encrypted to AGE_RECIPIENTS" in out
    assert f"AGE_IDENTITY's public key is {own_public}: add it to AGE_RECIPIENTS" in out
    assert "2 checks failed." in out


def test_doctor_missing_settings_and_unknown_host(env, monkeypatch, capsys):
    monkeypatch.delenv("AGE_RECIPIENTS")
    monkeypatch.delenv("AGE_IDENTITY_FILE")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@db-that-does-not-exist.invalid/app")
    assert main(["doctor", "--plain"]) == 1
    out = capsys.readouterr().out
    assert "✗ Settings: AGE_RECIPIENTS is not set" in out
    assert "- AGE_IDENTITY isn't set: fine for a backup-only server" in out
    assert "✗ Database: can't connect to db-that-does-not-exist.invalid/app" in out
    assert 'Nothing called "db-that-does-not-exist.invalid" can be found' in out
    assert "PGB_NETWORK=<project>_default" in out
    assert "pw" not in out.replace("pwd", "")


def test_backup_with_tiers_then_drill_a_copy(env, monkeypatch, capsys):
    from datetime import UTC, datetime

    from pgbackup_r2 import commands

    monkeypatch.setenv("TIERS", "daily=nightly/ monthly=monthly/")
    for when in ("2026-11-18T02:00:00", "2026-11-18T14:00:00"):  # twice on the same day
        moment = datetime.fromisoformat(when).replace(tzinfo=UTC)
        monkeypatch.setattr(commands, "utcnow", lambda moment=moment: moment)
        assert main(["backup", "--plain"]) == 0
    keys = objects(env["s3"])
    assert [k for k in keys if k.endswith(".dump.age")] == [
        f"{PREFIX}/monthly/2026-11-18T020000Z.dump.age",
        f"{PREFIX}/nightly/2026-11-18T020000Z.dump.age",
        f"{PREFIX}/nightly/2026-11-18T140000Z.dump.age",
    ]
    assert f"{PREFIX}/monthly/2026-11-18T020000Z.manifest.age" in keys

    capsys.readouterr()
    assert main(["drill", "monthly/2026-11-18T020000Z"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"PASS  {PREFIX}/monthly/2026-11-18T020000Z.dump.age")

    monkeypatch.setenv("PG_MAJOR", str(server_major(env["live"])))
    assert main(["doctor", "--plain"]) == 0
    out = capsys.readouterr().out
    assert f"✓ Tier daily: {PREFIX}/nightly/ gets every upload, 2 backups" in out
    assert f"✓ Tier monthly: {PREFIX}/monthly/ gets copies, 1 backup" in out
    assert "nightly/ has no lifecycle rule that deletes old files, and PRUNE is off" in out


def drill_records(s3) -> list[dict]:
    import json

    keys = sorted(k for k in objects(s3) if k.startswith(f"{PREFIX}/.drills/"))
    records = [json.loads(s3.get_object(Bucket=BUCKET, Key=k)["Body"].read()) for k in keys]
    return sorted(records, key=lambda r: r["drilled_at"])


def test_drill_records_pass_and_fail(env, capsys):
    assert main(["backup"]) == 0
    dump_key = dump_object(env["s3"])
    assert main(["drill"]) == 0
    sql(env["scratch"], "CREATE TABLE leftover (id int)")
    assert main(["drill"]) == 1

    passed, failed = drill_records(env["s3"])
    assert passed["result"] == "PASS" and failed["result"] == "FAIL"
    assert passed["backup"] == dump_key and passed["backup_stamp"] in dump_key
    assert (passed["tables"], passed["rows"]) == (2, 5250)
    assert failed["tables"] == 3  # the leftover table makes it fail
    assert passed["seconds"] >= 0 and passed["tool_version"]
    assert MARKER not in str(drill_records(env["s3"]))  # counts only, no data

    capsys.readouterr()
    assert main(["list"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("Last drill: FAIL, just now")
    # The records don't show up as backups, and `prune` leaves them alone.
    assert main(["prune"]) == 0
    assert len(drill_records(env["s3"])) == 2


def test_output_never_shows_credentials_or_query(env, monkeypatch, capsys, caplog):
    """Database URLs appear as host/dbname: no user, password, sslmode or channel_binding."""
    query = "?sslmode=disable&channel_binding=prefer"
    monkeypatch.setenv("DATABASE_URL", env["live"] + query)
    monkeypatch.setenv("DRILL_DATABASE_URL", env["scratch"] + query)
    monkeypatch.setenv("RESTORE_DATABASE_URL", env["scratch"] + query)
    monkeypatch.setenv("PG_MAJOR", str(server_major(env["live"])))
    password = urlsplit(ADMIN_URL).password or ""

    outputs = {}
    for cmd in (["backup"], ["list"], ["verify"], ["drill"], ["restore"], ["doctor"]):
        caplog.clear()
        main([*cmd, "--plain"])
        captured = capsys.readouterr()
        outputs[cmd[0]] = captured.out + captured.err + caplog.text
    for name, text in outputs.items():
        for line in text.splitlines():
            assert "sslmode" not in line and "channel_binding" not in line, (name, line)
            assert "@" not in line, (name, line)
            if password:
                assert f":{password}@" not in line and f"={password}" not in line, (name, line)
    # restore names its target once: in the result line, not in the step lines too.
    scratch = urlsplit(env["scratch"])
    shown = f"{scratch.hostname}{'' if scratch.port in (None, 5432) else f':{scratch.port}'}{scratch.path}"
    restore_lines = [ln for ln in outputs["restore"].splitlines() if shown in ln]
    assert len(restore_lines) == 1 and restore_lines[0].startswith("PASS  "), restore_lines
