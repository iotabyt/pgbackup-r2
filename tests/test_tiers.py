"""Bucket-managed retention (TIERS), detection, foreign files and locked deletes. S3 is moto."""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest

from pgbackup_r2 import commands
from pgbackup_r2.cli import main
from pgbackup_r2.config import Settings
from pgbackup_r2.errors import ConfigError
from pgbackup_r2.stamps import make_stamp, manifest_key
from pgbackup_r2.storage import Store
from pgbackup_r2.tiers import Tier, guess_tier, parse_tiers, period

BUCKET = "tiers-test"


@pytest.fixture
def s3(monkeypatch):
    from moto import mock_aws

    for name in ("S3_ENDPOINT", "TIERS", "PRUNE", "S3_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def keys(s3) -> list[str]:
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def settings(**env: str) -> Settings:
    base = {
        "S3_BUCKET": BUCKET,
        "S3_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
    }
    return Settings.from_env({**base, **env})


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def run_backup(store: Store, s3, when: datetime) -> str:
    """What `backup` does to the bucket, minus pg_dump: upload to the base tier, then copy."""
    stamp = make_stamp(when)
    key = store.key_for(stamp)
    s3.put_object(
        Bucket=BUCKET, Key=key, Body=b"age-encryption.org/v1 dump", Metadata={"sha256": stamp}
    )
    s3.put_object(Bucket=BUCKET, Key=manifest_key(key), Body=b"age-encryption.org/v1 manifest")
    commands.copy_to_tiers(store, stamp)
    return stamp


def stamps_in(s3, folder: str) -> list[str]:
    return [
        k[len(folder) :].removesuffix(".dump.age")
        for k in keys(s3)
        if k.startswith(folder) and k.endswith(".dump.age") and "/" not in k[len(folder) :]
    ]


# --- parsing --------------------------------------------------------------------------------


def test_parse_tiers_sorts_by_frequency_and_accepts_quotes():
    tiers = parse_tiers('"monthly=db/month/ daily=db/day"')
    assert tiers == (Tier("daily", "db/day"), Tier("monthly", "db/month"))
    assert parse_tiers(None) == () and parse_tiers("  ") == ()


@pytest.mark.parametrize(
    "raw, problem",
    [
        ("fortnightly=f/", "'fortnightly' isn't a tier name"),
        ("daily", "'daily' isn't tier=folder"),
        ("daily=", "daily has no folder"),
        ("daily=a/ daily=b/", "daily is listed twice"),
        ("daily=a/ monthly=a/", "two tiers use the folder a/"),
        ("daily=../up", "isn't a usable folder name"),
    ],
)
def test_bad_tiers_are_config_errors_with_an_example(raw, problem):
    with pytest.raises(ConfigError) as exc:
        parse_tiers(raw)
    assert problem in str(exc.value)
    assert 'TIERS="daily=daily/ monthly=monthly/"' in (exc.value.hint or "")


def test_prune_defaults_off_with_tiers():
    assert settings().prune is True
    assert settings(TIERS="daily=daily/").prune is False
    assert settings(TIERS="daily=daily/", PRUNE="true").prune is True
    with pytest.raises(ConfigError):
        settings(PRUNE="sometimes")


def test_periods():
    when = at("2027-01-01T02:00:00")  # a Friday in ISO week 53 of 2026
    assert period("daily", when) == "2027-01-01"
    assert period("weekly", when) == "2026-W53"
    assert period("monthly", when) == "2027-01"
    assert period("yearly", when) == "2027"
    assert period("hourly", when) != period("hourly", at("2027-01-01T02:00:01"))


@pytest.mark.parametrize(
    "name, tier",
    [
        ("daily", "daily"),
        ("nightly", "daily"),
        ("db-month", "monthly"),
        ("weekly-snapshots", "weekly"),
        ("Annual", "yearly"),
        ("misc", None),
        ("day-and-month", None),
    ],
)
def test_guess_tier(name, tier):
    assert guess_tier(name) == tier


# --- one copy per period --------------------------------------------------------------------


def test_daily_and_monthly(s3):
    store = Store(settings(TIERS="daily=daily/ monthly=monthly/"))
    runs = ["2026-11-18T02:00:00", "2026-11-18T14:00:00", "2026-11-19T02:00:00",
            "2026-12-01T02:00:00", "2026-12-01T14:00:00"]  # fmt: skip
    for r in runs:
        run_backup(store, s3, at(r))
    # Every run is uploaded to the most frequent tier, twice-a-day runs included.
    assert stamps_in(s3, "daily/") == [make_stamp(at(r)) for r in runs]
    # One copy per month: the first backup of November and of December.
    assert stamps_in(s3, "monthly/") == ["2026-11-18T020000Z", "2026-12-01T020000Z"]
    # Copies come with their manifests and keep the metadata (no re-upload).
    assert "monthly/2026-11-18T020000Z.manifest.age" in keys(s3)
    head = s3.head_object(Bucket=BUCKET, Key="monthly/2026-11-18T020000Z.dump.age")
    assert head["Metadata"]["sha256"] == "2026-11-18T020000Z"


def test_custom_folders_under_a_prefix_nightly_weekly_yearly(s3):
    store = Store(
        settings(
            S3_PREFIX="acme",
            TIERS="yearly=archive/years/ daily=backups/nightly/ weekly=backups/week/",
        )
    )
    runs = [
        "2026-12-28T02:00:00",  # Monday, ISO week 2026-W53
        "2026-12-28T14:00:00",  # same day again
        "2026-12-31T02:00:00",
        "2027-01-01T02:00:00",  # still 2026-W53, but a new year
        "2027-01-04T02:00:00",  # Monday, 2027-W01
        "2027-01-04T14:00:00",
    ]
    for r in runs:
        run_backup(store, s3, at(r))
    assert len(stamps_in(s3, "acme/backups/nightly/")) == 6
    assert stamps_in(s3, "acme/backups/week/") == ["2026-12-28T020000Z", "2027-01-04T020000Z"]
    assert stamps_in(s3, "acme/archive/years/") == ["2026-12-28T020000Z", "2027-01-01T020000Z"]
    assert not [k for k in keys(s3) if not k.startswith("acme/")]


def test_coverage_is_read_from_the_bucket(s3):
    store = Store(settings(TIERS="daily=daily/ monthly=monthly/"))
    run_backup(store, s3, at("2026-11-18T02:00:00"))
    # Someone deletes the month's copy: the next run of the month notices and copies again.
    s3.delete_object(Bucket=BUCKET, Key="monthly/2026-11-18T020000Z.dump.age")
    run_backup(store, s3, at("2026-11-19T02:00:00"))
    assert stamps_in(s3, "monthly/") == ["2026-11-19T020000Z"]
    # And a monthly backup put there by hand counts as covering its month.
    s3.put_object(Bucket=BUCKET, Key="monthly/2026-12-01T000000Z.dump.age", Body=b"x")
    run_backup(store, s3, at("2026-12-02T02:00:00"))
    assert stamps_in(s3, "monthly/") == ["2026-11-19T020000Z", "2026-12-01T000000Z"]


# --- reading across tiers -------------------------------------------------------------------


def test_list_resolve_and_latest_across_tiers(s3, capsys):
    s = settings(TIERS="daily=daily/ monthly=monthly/")
    store = Store(s)
    run_backup(store, s3, at("2026-10-01T02:00:00"))
    run_backup(store, s3, at("2026-11-18T02:00:00"))
    s3.delete_object(Bucket=BUCKET, Key="daily/2026-10-01T020000Z.dump.age")  # expired from daily

    latest = store.resolve("latest")
    assert (latest.tier, latest.stamp) == ("daily", "2026-11-18T020000Z")
    assert store.resolve("monthly/2026-11-18T020000Z").key == "monthly/2026-11-18T020000Z.dump.age"
    # A bare stamp prefers the most frequent tier, and finds one that's only in monthly/.
    assert store.resolve("2026-11-18T020000Z").tier == "daily"
    assert store.resolve("2026-10-01T020000Z").tier == "monthly"
    assert store.resolve("monthly/2026-10-01T020000Z.dump.age").tier == "monthly"
    with pytest.raises(Exception, match="no backup 2026-10-01T020000Z in the daily tier"):
        store.resolve("daily/2026-10-01T020000Z")

    assert commands.list_backups(s) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "monthly  2026-10-01T020000Z  26 B  monthly/2026-10-01T020000Z.dump.age",
        "daily    2026-11-18T020000Z  26 B  daily/2026-11-18T020000Z.dump.age",
        "monthly  2026-11-18T020000Z  26 B  monthly/2026-11-18T020000Z.dump.age",
        "Last drill: never. Run `pgb drill` to prove a backup restores.",
    ]


# --- foreign files --------------------------------------------------------------------------


def test_foreign_files_are_ignored_untouched_and_counted(s3, monkeypatch, capsys):
    foreign = ["daily/app-2025-01-01.sql.gz", "daily/README.txt", "daily/2026-01-01.dump"]
    for k in foreign:
        s3.put_object(Bucket=BUCKET, Key=k, Body=b"not ours")
    s = settings(
        TIERS="daily=daily/ monthly=monthly/", PRUNE="true", RETENTION_DAYS="1", MIN_KEEP="1"
    )
    store = Store(s)
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        run_backup(store, s3, at(f"{day}T02:00:00"))
    assert [b.stamp for b in store.list_tier(store.tiers[0])] == [
        "2026-11-01T020000Z", "2026-11-02T020000Z", "2026-11-03T020000Z",
    ]  # fmt: skip

    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-20T00:00:00"))
    commands.prune_backups(s, store=store)
    left = keys(s3)
    assert all(k in left for k in foreign)
    assert stamps_in(s3, "daily/") == ["2026-11-03T020000Z"]  # MIN_KEEP=1
    assert stamps_in(s3, "monthly/") == ["2026-11-01T020000Z"]  # never pruned by the tool

    capsys.readouterr()
    assert main(["detect", "--plain"]) == 0
    assert "3 other files in daily/ not made by pgbackup-r2 (left alone)" in capsys.readouterr().out


# --- detection ------------------------------------------------------------------------------


def lifecycle(s3, *rules: tuple[str, int]) -> None:
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET,
        LifecycleConfiguration={
            "Rules": [
                {"ID": p, "Status": "Enabled", "Filter": {"Prefix": p}, "Expiration": {"Days": d}}
                for p, d in rules
            ]
        },
    )


def test_detect_suggests_tiers_from_folders_and_lifecycle(s3, monkeypatch, capsys):
    monkeypatch.setenv("S3_PREFIX", "shop")
    for k in ("shop/daily/2026-11-18T020000Z.dump.age", "shop/monthly/x.sql", "shop/misc/a"):
        s3.put_object(Bucket=BUCKET, Key=k, Body=b"x")
    lifecycle(s3, ("shop/daily/", 30), ("shop/monthly/", 365))

    assert main(["detect", "--env-lines"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ['TIERS="daily=daily/ monthly=monthly/"', "PRUNE=false"]
    assert (
        "Found folders daily/ (lifecycle: delete after 30 days), misc/ and monthly/ "
        "(delete after 365 days)." in captured.err
    )
    assert "1 other file in monthly/ not made by pgbackup-r2" in captured.err
    assert "Not used as tiers (the name doesn't say how often): misc/" in captured.err

    # Once TIERS is set to the suggestion, there's nothing more to suggest.
    monkeypatch.setenv("TIERS", "daily=daily/ monthly=monthly/")
    assert main(["detect", "--env-lines"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Your TIERS already matches" in captured.err


def test_detect_with_custom_names_and_no_lifecycle(s3, capsys):
    for k in ("nightly/a", "weekly-snapshots/b", "Annual/c"):
        s3.put_object(Bucket=BUCKET, Key=k, Body=b"x")
    assert main(["detect", "--env-lines"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        'TIERS="daily=nightly/ weekly=weekly-snapshots/ yearly=Annual/"'
    )
    assert "lifecycle" not in captured.err  # no rules: nothing claimed about them


def test_detect_skips_lifecycle_quietly_when_not_allowed(s3):
    store = Store(settings())
    s3.put_object(Bucket=BUCKET, Key="daily/a", Body=b"x")

    def denied(**kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetLifecycle")

    store.client.get_bucket_lifecycle_configuration = denied
    layout = store.detect_layout()
    assert layout.lifecycle_readable is False
    assert [t.name for t in layout.suggestion] == ["daily"]


def test_detect_finds_nothing_in_a_flat_bucket(s3, capsys):
    s3.put_object(Bucket=BUCKET, Key="2026-11-18T020000Z.dump.age", Body=b"x")
    assert main(["detect", "--plain"]) == 0
    assert "No tier folders" in capsys.readouterr().out


# --- locked deletes -------------------------------------------------------------------------


def test_locked_delete_is_a_warning_not_a_failure(s3, monkeypatch, caplog):
    s = settings(RETENTION_DAYS="1", MIN_KEEP="1")
    store = Store(s)
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        key = f"{day}T020000Z.dump.age"
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        s3.put_object(Bucket=BUCKET, Key=manifest_key(key), Body=b"x")

    real = store.client.delete_objects
    locked = {"2026-11-01T020000Z.dump.age", "2026-11-01T020000Z.manifest.age"}

    def delete_objects(Bucket, Delete):  # noqa: N803 - boto3's argument names
        allowed = [o for o in Delete["Objects"] if o["Key"] not in locked]
        if allowed:
            real(Bucket=Bucket, Delete={"Objects": allowed, "Quiet": True})
        return {
            "Errors": [
                {
                    "Key": k,
                    "Code": "AccessDenied",
                    "Message": "object is protected by a bucket lock",
                }
                for k in sorted(locked)
            ]
        }

    store.client.delete_objects = delete_objects
    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-20T00:00:00"))
    assert commands.prune_backups(s, store=store) == 0
    assert "2026-11-01T020000Z.dump.age: locked, skipped" in caplog.text
    assert "pruned 1 backups" in caplog.text
    assert keys(s3) == [
        "2026-11-01T020000Z.dump.age", "2026-11-01T020000Z.manifest.age",
        "2026-11-03T020000Z.dump.age", "2026-11-03T020000Z.manifest.age",
    ]  # fmt: skip


def test_other_delete_errors_still_fail(s3):
    store = Store(settings())
    store.client.delete_objects = lambda **kw: {
        "Errors": [{"Key": "k", "Code": "InternalError", "Message": "boom"}]
    }
    with pytest.raises(Exception, match="couldn't delete 1 object"):
        store.delete(["k"])


def test_doctor_reports_a_locked_test_file(s3, monkeypatch, capsys):
    from pgbackup_r2.storage import Store as RealStore

    def locked_delete(self, keys):
        return list(keys)

    monkeypatch.setattr(RealStore, "delete", locked_delete)
    monkeypatch.setenv("S3_PREFIX", "shop")
    assert main(["doctor", "--plain"]) == 1  # other settings are missing; the bucket is fine
    out = capsys.readouterr().out
    assert (
        "✓ Bucket: s3://tiers-test/shop/ is writable. The test file shop/.pgbackup-r2-doctor-"
        in out
    )
    assert "is locked by a lock or retention rule" in out


# --- drill records --------------------------------------------------------------------------


def put_drill(store: Store, when: str, result: str) -> None:
    moment = at(when)
    store.put_drill(
        make_stamp(moment),
        {"drilled_at": moment.isoformat().replace("+00:00", "Z"), "result": result, "backup": "k"},
    )


def drill_lines(store: Store) -> list[tuple]:
    lines: list[tuple] = []
    commands._drill_check(store, lambda ok, label, fix=None: lines.append((ok, label, fix)))
    return lines


def test_doctor_drill_ages(s3, monkeypatch):
    monkeypatch.setenv("S3_PREFIX", "shop")
    store = Store(settings(S3_PREFIX="shop"))
    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-30T12:00:00"))

    assert drill_lines(store) == [
        ("warn", "This bucket has never been drilled.", "Run `pgb drill`.")
    ]

    put_drill(store, "2026-10-20T12:00:00", "PASS")  # 41 days before
    assert drill_lines(store) == [
        ("warn", "Your last successful drill was 41 days ago.", "Run `pgb drill`.")
    ]

    put_drill(store, "2026-11-18T12:00:00", "PASS")  # 12 days before
    [(ok, label, _)] = drill_lines(store)
    assert ok is True and label.startswith("Last successful drill: 12 days ago")

    put_drill(store, "2026-11-29T12:00:00", "FAIL")
    lines = drill_lines(store)
    assert lines[0][0] is True and "12 days ago" in lines[0][1]
    assert lines[1][:2] == ("warn", "The most recent drill failed (1 day ago).")

    # Over 35 days counts from the last PASS, not the last drill.
    monkeypatch.setattr(commands, "utcnow", lambda: at("2027-01-10T12:00:00"))
    assert drill_lines(store)[0][1] == "Your last successful drill was 53 days ago."


def test_doctor_drill_never_passed(s3, monkeypatch):
    store = Store(settings())
    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-30T12:00:00"))
    put_drill(store, "2026-11-27T12:00:00", "FAIL")
    assert drill_lines(store)[0][:2] == (
        "warn",
        "No drill has passed yet (last drill: FAIL, 3 days ago).",
    )


@pytest.mark.parametrize("tiers", [None, "daily=daily/ monthly=monthly/"])
def test_list_shows_last_drill_and_ignores_records(s3, monkeypatch, capsys, tiers):
    env = {"S3_PREFIX": "shop", **({"TIERS": tiers} if tiers else {})}
    s = settings(**env)
    store = Store(s)
    run_backup(store, s3, at("2026-11-18T02:00:00"))
    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-30T12:00:00"))

    assert commands.list_backups(s) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("Last drill: never.")

    put_drill(store, "2026-11-18T03:00:00", "PASS")
    assert [b.stamp for b in store.list_backups()] == (["2026-11-18T020000Z"] * (2 if tiers else 1))
    assert commands.list_backups(s) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Last drill: PASS, 12 days ago"
    assert ".drills" not in store.top_folders()


@pytest.mark.parametrize("tiers", [None, "daily=daily/ monthly=monthly/"])
def test_prune_never_touches_drill_records(s3, monkeypatch, tiers):
    env = {
        "RETENTION_DAYS": "1",
        "MIN_KEEP": "1",
        "PRUNE": "true",
        **({"TIERS": tiers} if tiers else {}),
    }
    s = settings(**env)
    store = Store(s)
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        run_backup(store, s3, at(f"{day}T02:00:00"))
        put_drill(store, f"{day}T03:00:00", "PASS")
    monkeypatch.setattr(commands, "utcnow", lambda: at("2026-11-30T12:00:00"))
    assert commands.prune_backups(s, store=store) == 0
    records = [k for k in keys(s3) if k.startswith(".drills/")]
    assert len(records) == 3
    assert (
        len([k for k in keys(s3) if k.endswith(".dump.age") and not k.startswith("monthly/")]) == 1
    )


def test_two_drills_in_the_same_second_both_count(s3, monkeypatch):
    store = Store(settings())
    stamp = "2026-11-18T120000Z"
    store.put_drill(stamp, {"drilled_at": "2026-11-18T12:00:00.100000Z", "result": "PASS"})
    store.put_drill(stamp, {"drilled_at": "2026-11-18T12:00:00.900000Z", "result": "FAIL"})
    assert len(store.drill_keys()) == 2
    last, last_pass = store.last_drills()
    assert last["result"] == "FAIL" and last_pass["result"] == "PASS"


# --- doctor: typos in .env ------------------------------------------------------------------


def test_doctor_catches_misspelt_settings(s3, monkeypatch, capsys):
    # Through pgb: PGB_ENV_KEYS lists the names in .env, so every unknown one is reported.
    monkeypatch.setenv("PGB_ENV_KEYS", "S3_BUCKET,S3_BUKET,DATABSE_URL,COMPOSE_PROJECT,")
    main(["doctor", "--plain"])
    out = capsys.readouterr().out
    assert "⚠ .env: S3_BUKET → S3_BUCKET? pgbackup-r2 has no setting called S3_BUKET." in out
    assert "    Rename it to S3_BUCKET." in out
    assert "⚠ .env: DATABSE_URL → DATABASE_URL?" in out
    assert "⚠ .env: COMPOSE_PROJECT isn't a pgbackup-r2 setting, so nothing reads it." in out
    assert "S3_BUCKET →" not in out and "PGB_ENV_KEYS" not in out


def test_doctor_without_pgb_only_flags_look_alikes(s3, monkeypatch, capsys):
    # No PGB_ENV_KEYS: the whole environment is there, so only likely typos count.
    monkeypatch.delenv("PGB_ENV_KEYS", raising=False)
    monkeypatch.setenv("RETENTION_DAY", "14")
    monkeypatch.setenv("SOME_OTHER_TOOL_SETTING", "x")
    main(["doctor", "--plain"])
    out = capsys.readouterr().out
    assert "⚠ Environment: RETENTION_DAY → RETENTION_DAYS?" in out
    assert "SOME_OTHER_TOOL_SETTING" not in out


# --- errors always say what to do next ------------------------------------------------------


def test_every_config_error_has_a_hint():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "pgbackup_r2"
    missing = []
    for path in sorted(src.glob("*.py")):
        text = path.read_text()
        for m in re.finditer(
            r"raise (ConfigError|PgBackupError)\((.*?)\)\s*(?:from \w+)?\n", text, re.S
        ):
            if "hint=" not in m.group(2) and "bad(" not in m.group(0):
                missing.append(f"{path.name}:{text[: m.start()].count(chr(10)) + 1}")
    assert missing == []


def test_errors_without_their_own_hint_point_at_doctor():
    from botocore.exceptions import EndpointConnectionError

    from pgbackup_r2.cli import DOCTOR_HINT, _hint
    from pgbackup_r2.errors import CommandError

    assert _hint(CommandError("pg_dump failed (exit 1): something")) == DOCTOR_HINT
    assert _hint(RuntimeError("anything")) == DOCTOR_HINT
    assert "S3_ENDPOINT" in _hint(EndpointConnectionError(endpoint_url="http://x"))
    assert _hint(CommandError("x", hint="Do this")) == "Do this"
