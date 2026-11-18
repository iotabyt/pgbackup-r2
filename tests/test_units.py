import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from pgbackup_r2 import agecrypt, heartbeat
from pgbackup_r2.checks import compare_counts, quote_ident, split_qualified, toc_tables
from pgbackup_r2.config import Settings
from pgbackup_r2.errors import ConfigError
from pgbackup_r2.retention import select_expired
from pgbackup_r2.scheduling import next_run, parse_times, sleep_until
from pgbackup_r2.stamps import dump_key, make_stamp, manifest_key, normalize_stamp
from pgbackup_r2.util import human_size

NOW = datetime(2026, 11, 18, 2, 0, 30, tzinfo=UTC)


# --- retention --------------------------------------------------------------------------


def daily(n, start=NOW):
    return [make_stamp(start - timedelta(days=i)) for i in range(n)]


def test_sixty_daily_backups_thirty_days_retention():
    expired = select_expired(daily(60), NOW, retention_days=30, min_keep=7)
    assert len(expired) == 29
    assert expired == sorted(expired)  # oldest first
    assert make_stamp(NOW - timedelta(days=59)) == expired[0]


def test_min_keep_protects_newest_even_if_ancient():
    ancient = daily(20, start=NOW - timedelta(days=400))
    expired = select_expired(ancient, NOW, retention_days=30, min_keep=7)
    kept = set(ancient) - set(expired)
    assert len(kept) == 7
    assert kept == set(sorted(ancient)[-7:])


def test_nothing_expires_when_few_backups():
    assert select_expired(daily(3, NOW - timedelta(days=90)), NOW, 30, 7) == []


# --- names ------------------------------------------------------------------------------


def test_manifest_key_name():
    key = dump_key("labpadi", "2026-11-18T020000Z")
    assert key == "labpadi/2026-11-18T020000Z.dump.age"
    assert manifest_key(key) == "labpadi/2026-11-18T020000Z.manifest.age"
    assert dump_key("", "2026-11-18T020000Z") == "2026-11-18T020000Z.dump.age"
    assert dump_key("/a/b/", "2026-11-18T020000Z") == "a/b/2026-11-18T020000Z.dump.age"


def test_normalize_stamp():
    assert normalize_stamp("labpadi/2026-11-18T020000Z.dump.age") == "2026-11-18T020000Z"
    with pytest.raises(Exception, match="isn't a backup stamp"):
        normalize_stamp("2026-13-45T999999Z")


# --- schedule ---------------------------------------------------------------------------


def test_parse_times():
    assert parse_times("02:00") == [(2, 0)]
    assert parse_times(" 14:30, 2:05 ,02:05") == [(2, 5), (14, 30)]


@pytest.mark.parametrize("bad", ["", "24:00", "12:60", "2", "02:5", "ab:cd", "02:00,", "1:2:3"])
def test_parse_times_rejects_bad_input(bad):
    if bad == "02:00,":  # a trailing comma is harmless
        assert parse_times(bad) == [(2, 0)]
        return
    with pytest.raises(ConfigError):
        parse_times(bad)


def test_next_run_same_day():
    now = datetime(2026, 11, 18, 1, 59, 59, tzinfo=UTC)
    assert next_run(now, [(2, 0), (14, 0)]) == datetime(2026, 11, 18, 2, 0, tzinfo=UTC)
    now = datetime(2026, 11, 18, 2, 0, 0, tzinfo=UTC)  # exactly now -> the next one
    assert next_run(now, [(2, 0), (14, 0)]) == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)


def test_next_run_next_day():
    now = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert next_run(now, [(2, 0), (14, 0)]) == datetime(2027, 1, 1, 2, 0, tzinfo=UTC)


def test_sleep_is_chunked_to_five_minutes():
    clock = [NOW]
    naps = []

    def fake_sleep(seconds):
        naps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    sleep_until(NOW + timedelta(minutes=12), now=lambda: clock[0], sleep=fake_sleep)
    assert naps == [300, 300, 120]


# --- drill comparison -------------------------------------------------------------------


def test_compare_counts():
    expected = {"public.a": 1000, "public.b": 0, "public.gone": 5, "public.c": 50}
    actual = {"public.a": 1010, "public.b": 0, "public.c": 49, "public.new": 3}
    problems = compare_counts(expected, actual, 0.01)
    assert len(problems) == 2
    assert problems[0].startswith("missing table public.gone")
    assert problems[1].startswith("extra table public.new")
    # 1000 rows -> 10 allowed; 50 rows -> at least 1 allowed
    assert compare_counts({"t": 1000}, {"t": 1011}, 0.01)[0].startswith("t: expected 1,000")
    assert compare_counts({"t": 0}, {"t": 1}, 0.5) != []


def test_quote_ident_round_trip():
    assert quote_ident("schools") == "schools"
    assert quote_ident("Readings") == '"Readings"'
    assert split_qualified('public."Readings"') == ("public", "Readings")
    assert split_qualified('"My Schema"."a""b"') == ("My Schema", 'a"b')
    assert split_qualified("public.schools") == ("public", "schools")


def test_toc_tables():
    toc = """;
; Archive created at 2026-11-18 02:00:00 UTC
;     Dumped from database version: 16.4
215; 1259 16390 TABLE public schools postgres
216; 1259 16391 SEQUENCE public schools_id_seq postgres
217; 1259 16400 TABLE public Readings postgres
3345; 0 16390 TABLE DATA public schools postgres
"""
    assert toc_tables(toc) == [("public", "Readings"), ("public", "schools")]


# --- misc -------------------------------------------------------------------------------


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(48_200_000) == "48.2 MB"
    assert human_size(1_500) == "1.5 kB"


def test_settings_from_env():
    s = Settings.from_env(
        {"S3_BUCKET": "b", "AGE_RECIPIENTS": "age1aaa, age1bbb,", "RETENTION_DAYS": "14"}
    )
    assert s.age_recipients == ("age1aaa", "age1bbb")
    assert s.retention_days == 14 and s.min_keep == 7 and s.s3_region == "auto"
    with pytest.raises(ConfigError, match="S3_BUCKET"):
        Settings.from_env({})
    assert Settings.from_env({}, require_bucket=False).s3_bucket is None
    with pytest.raises(ConfigError, match="both AGE_IDENTITY and AGE_IDENTITY_FILE"):
        Settings.from_env({"S3_BUCKET": "b", "AGE_IDENTITY": "x", "AGE_IDENTITY_FILE": "y"})
    with pytest.raises(ConfigError, match="MIN_KEEP"):
        Settings.from_env({"S3_BUCKET": "b", "MIN_KEEP": "0"})
    with pytest.raises(ConfigError, match="DATABASE_URL|scheme"):
        Settings.from_env({"S3_BUCKET": "b", "DATABASE_URL": "mysql://x/y"})


def test_settings_repr_hides_secrets():
    s = Settings.from_env(
        {
            "S3_BUCKET": "b",
            "DATABASE_URL": "postgresql://u:hunter2@h/db",
            "AGE_IDENTITY": "AGE-SECRET-KEY-1SECRET",
        }
    )
    assert "hunter2" not in repr(s) and "SECRET" not in repr(s)


def test_recipients_required():
    with pytest.raises(ConfigError, match="AGE_RECIPIENTS is empty"):
        agecrypt.check_recipients(())
    with pytest.raises(ConfigError, match="private key"):
        agecrypt.check_recipients(("AGE-SECRET-KEY-1XYZ",))


def test_identity_validation_messages():
    with pytest.raises(ConfigError, match="looks like a public key"):
        agecrypt.validate_identity("age1qqqqqqqq")
    with pytest.raises(ConfigError, match="typo"):
        agecrypt.validate_identity("AGE-SECRET-KEY")
    assert agecrypt.validate_identity("# comment\nAGE-SECRET-KEY-1ABC\n") == "AGE-SECRET-KEY-1ABC\n"


def test_identity_temp_file_is_private_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    s = Settings(age_identity="AGE-SECRET-KEY-1ABC")
    with agecrypt.identity_path(s) as path:
        with open(path) as f:
            assert f.read() == "AGE-SECRET-KEY-1ABC\n"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert os.listdir(tmp_path) == []

    with pytest.raises(RuntimeError), agecrypt.identity_path(s):
        raise RuntimeError("age blew up")
    assert os.listdir(tmp_path) == []


def test_heartbeat_failure_is_only_a_warning(caplog):
    heartbeat.ping("http://127.0.0.1:9/nothing-listens-here")
    heartbeat.ping("http://127.0.0.1:9/nothing", failure="boom")
    heartbeat.ping(None)
    assert "heartbeat ping" in caplog.text


# --- output mode ------------------------------------------------------------------------


def test_should_be_interactive():
    from pgbackup_r2.ui import should_be_interactive as decide

    tty = {"stdout_isatty": True, "plain": False, "command": "drill"}
    assert decide(env={}, **tty)
    assert not decide(env={"NO_COLOR": "1"}, **tty)
    assert not decide(env={"TERM": "dumb"}, **tty)
    assert not decide(env={}, **{**tty, "plain": True})
    assert not decide(env={}, **{**tty, "command": "schedule"})  # always plain
    assert not decide(env={}, **{**tty, "stdout_isatty": False})


def test_ago_and_count_report():
    from pgbackup_r2.checks import count_report
    from pgbackup_r2.util import ago

    assert ago(NOW - timedelta(hours=6, minutes=5), NOW) == "6 hours ago"
    assert ago(NOW - timedelta(days=1), NOW) == "1 day ago"
    assert ago(NOW - timedelta(seconds=20), NOW) == "just now"
    report = count_report({"a": 10, "b": 5}, {"a": 10, "c": 1}, 0.01)
    assert report == [("a", 10, 10, True), ("b", 5, None, False), ("c", None, 1, False)]


def test_restore_target_prefers_flag_then_env():
    s = Settings.from_env({"S3_BUCKET": "b", "RESTORE_DATABASE_URL": "postgresql://u:pw@h/r"})
    assert s.restore_target(None) == "postgresql://u:pw@h/r"
    assert s.restore_target("postgresql://h/other") == "postgresql://h/other"
    assert "pw" not in repr(s)
    with pytest.raises(ConfigError, match="no restore target"):
        Settings.from_env({"S3_BUCKET": "b"}).restore_target(None)
    with pytest.raises(ConfigError):
        Settings.from_env({"S3_BUCKET": "b", "RESTORE_DATABASE_URL": "mysql://h/db"})


@pytest.mark.parametrize("columns", [120, 80])
def test_keygen_panels_same_width_and_keys_never_cut(columns):
    import io

    from rich.console import Console

    from pgbackup_r2 import ui

    public = "age1" + "q" * 58
    secret = "AGE-SECRET-KEY-1" + "Q" * 58
    buf = io.StringIO()
    old = (ui._state.interactive, ui._state.console)
    ui._state.interactive = True
    ui._state.console = Console(file=buf, width=columns, highlight=False, color_system=None)
    try:
        ui.keys(public, secret, "")
    finally:
        ui._state.interactive, ui._state.console = old
    lines = buf.getvalue().splitlines()
    # Whole key lines, never truncated or wrapped, so copying one gets the full key.
    assert any(line.strip() == f"AGE_RECIPIENTS={public}" for line in lines)
    assert any(line.strip() == f"AGE_IDENTITY={secret}" for line in lines)
    borders = [line.rstrip() for line in lines if line.strip().startswith("─")]
    if columns == 120:
        assert len(borders) == 4  # top and bottom of each panel
        assert len({len(b) for b in borders}) == 1


def test_connection_hint():
    from pgbackup_r2.pg import connection_hint

    text = (
        'psql failed (exit 2): psql: error: could not translate host name "db" to address: '
        "Name or service not known"
    )
    hint = connection_hint(text)
    assert hint and 'Nothing called "db"' in hint
    assert "PGB_NETWORK=<project>_default" in hint and "localhost" in hint
    refused = (
        'connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection refused'
    )
    assert "localhost is the container itself" in connection_hint(
        refused, "postgresql://a:b@localhost/x"
    )
    assert connection_hint(refused, "postgresql://a:b@db.example.com/x") is None
    assert connection_hint("permission denied for table x") is None


@pytest.mark.parametrize(
    "error, words",
    [
        ('FATAL:  password authentication failed for user "app"', "user or password"),
        ('FATAL:  database "shop" does not exist', "database name"),
        ("timeout expired", "host and port"),
        ("FATAL:  no pg_hba.conf entry for host, SSL off", "sslmode=require"),
    ],
)
def test_connection_hint_for_common_failures(error, words):
    from pgbackup_r2.pg import connection_hint

    assert words in (connection_hint(f"psql failed (exit 2): {error}") or "")
