"""The commands. Each returns an exit code or raises a PgBackupError / boto error.

All output goes through `ui`, which decides between interactive and plain mode.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, agecrypt, heartbeat, hints, pg, ui
from .checks import (
    compare_counts,
    count_report,
    dumped_from,
    quote_ident,
    split_qualified,
    toc_tables,
)
from .config import Settings, endpoint_base, endpoint_path, unknown_settings
from .errors import CommandError, PgBackupError
from .pgurl import parse_url, same_database
from .pgurl import short as short_url
from .retention import select_expired
from .scheduling import format_times, next_run, parse_times, sleep_until
from .stamps import DUMP_SUFFIX, MANIFEST_SUFFIX, make_stamp, manifest_key, parse_stamp
from .storage import DRILLS_FOLDER, Backup, BucketError, Store, nested_line, tiers_hint
from .tiers import Layout, format_tiers, period, rule_for
from .util import ago, human_size, require_tools, run, sha256_file, workdir

MANIFEST_NOTE = (
    "Row counts are taken just before the dump starts, in a separate transaction, "
    "so a busy table can drift slightly from what the dump contains."
)

KEYGEN_TEMPLATE = """\
# Your new key pair. Do this once.
# 1. Save the PRIVATE key in your password manager now. Lose it and your backups can't be opened.
# 2. Put the PUBLIC key on your server.

# server .env (public key, safe to share):
AGE_RECIPIENTS={public}

# your laptop's .env, for restores and drills (keep this secret):
AGE_IDENTITY={secret}"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def _n(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


# --- backup -----------------------------------------------------------------------------


def backup(settings: Settings, *, prune: bool | None = None) -> str:
    """Take one encrypted backup and upload it with its manifest. Returns the dump key.

    With TIERS, the upload goes to the most frequent tier and other tiers get server-side
    copies. `prune` defaults to PRUNE (on without TIERS, off with it)."""
    url = settings.require_database_url()
    recipients = agecrypt.check_recipients(settings.age_recipients)
    require_tools("psql", "pg_dump", "age")
    store = Store(settings)

    started = time.monotonic()
    taken_at = utcnow()
    stamp = make_stamp(taken_at)
    key = store.key_for(stamp)

    with ui.step(
        "Counting rows",
        log_line=f"backing up {short_url(url)} to s3://{store.bucket}/{key}",
    ) as s:
        version = pg.server_version(url)
        # pg_dump refuses a newer server; say why now, not as its own error after a while.
        pg.check_can_dump(pg.server_major(url), pg.client_major())
        tables = pg.table_counts(url)
        s.done = f"Counted rows in {len(tables)} tables"
        s.detail = f"(PostgreSQL {version.split()[0]})"

    with workdir() as tmp:
        path = os.path.join(tmp, f"{stamp}{DUMP_SUFFIX}")
        with ui.step("Dumping and encrypting") as s:
            pg.dump_encrypted(url, recipients, path)
            digest = sha256_file(path)
            size = os.path.getsize(path)
            s.done = f"Dumped and encrypted ({human_size(size)})"
        with ui.step(f"Uploading {key}", done=f"Uploaded {key}"):
            store.upload_file(
                path, key, {"sha256": digest, "pg-version": version, "tool-version": __version__}
            )

    manifest = {
        "stamp": stamp,
        "taken_at": taken_at.isoformat().replace("+00:00", "Z"),
        "pg_version": version,
        "tool_version": __version__,
        "sha256": digest,
        "size": size,
        "tables": tables,
        "note": MANIFEST_NOTE,
    }
    with ui.step("Uploading the encrypted manifest", done="Uploaded the encrypted manifest"):
        sealed = agecrypt.encrypt_bytes(json.dumps(manifest, indent=2).encode(), recipients)
        store.put_bytes(manifest_key(key), sealed)

    ui.info(
        f"backed up {key} ({human_size(size)}, {len(tables)} tables) "
        f"in {time.monotonic() - started:.1f}s"
    )
    if settings.tiers:
        copy_to_tiers(store, stamp)
    if settings.prune if prune is None else prune:
        prune_backups(settings, store=store)
    heartbeat.ping(settings.heartbeat_url)
    return key


def copy_to_tiers(store: Store, stamp: str) -> list[str]:
    """Copy the backup `stamp` from the most frequent tier into every other tier whose current
    period has no backup yet. Coverage is read from the bucket. Returns the tiers copied to."""
    tiers = store.tiers
    if len(tiers) < 2:
        return []
    src = store.key_for(stamp)
    when = parse_stamp(stamp)
    copied = []
    for tier in tiers[1:]:
        now_period = period(tier.name, when)
        have = {period(tier.name, b.taken_at) for b in store.list_tier(tier)}
        if now_period in have:
            ui.info(f"{tier.name}: {tier.folder}/ already has a backup from {now_period}")
            continue
        dst = store.tier_key(tier, stamp)
        with ui.step(f"Copying to {tier.folder}/", done=f"Copied to {dst} (first of {now_period})"):
            store.copy(src, dst)
            store.copy(manifest_key(src), manifest_key(dst))
        ui.info(f"{tier.name}: copied to {dst}, the first backup of {now_period}")
        copied.append(tier.name)
    return copied


# --- list / prune -----------------------------------------------------------------------


def list_backups(settings: Settings) -> int:
    store = Store(settings)
    with ui.step(f"Listing {store.where()}", done=f"Listed {store.where()}"):
        backups = store.list_backups()
    if not backups:
        nested = store.nested_backups()
        if nested:
            ui.warn(f"no backups directly under {store.where()}, but {nested_line(nested)}")
            ui.out(tiers_hint(nested))
            return 0
        ui.out("no backups yet")
        return 0
    try:
        last_drill = _last_drill_line(store)
    except (ClientError, BotoCoreError, BucketError):
        last_drill = None
    now = utcnow()
    width = max(len(human_size(b.size)) for b in backups)
    title = f"{len(backups)} backup{'s' if len(backups) != 1 else ''} in {store.where()}"
    if not settings.tiers:
        ui.table(
            ["stamp", "age", "size", "key"],
            [[b.stamp, ago(b.taken_at, now), human_size(b.size), b.key] for b in backups],
            align="llrl",
            title=title,
            plain_lines=[f"{b.stamp}  {human_size(b.size):>{width}}  {b.key}" for b in backups],
        )
        if last_drill:
            ui.out(last_drill)
        return 0
    tw = max(len(t.name) for t in settings.tiers)
    ui.table(
        ["tier", "stamp", "age", "size", "key"],
        [[b.tier, b.stamp, ago(b.taken_at, now), human_size(b.size), b.key] for b in backups],
        align="lllrl",
        title=title,
        plain_lines=[
            f"{b.tier:<{tw}}  {b.stamp}  {human_size(b.size):>{width}}  {b.key}" for b in backups
        ],
    )
    if last_drill:
        ui.out(last_drill)
    return 0


def prune_backups(settings: Settings, *, dry_run: bool = False, store: Store | None = None) -> int:
    """Delete backups past RETENTION_DAYS, keeping MIN_KEEP. With TIERS, only the most frequent
    tier is pruned; the other tiers are left to the bucket's lifecycle rules."""
    store = store or Store(settings)
    if settings.tiers:
        base = settings.tiers[0]
        backups = {b.stamp: b for b in store.list_tier(base)}
        ui.info(f"prune: only the {base.name} tier ({base.folder}/); other tiers are left alone")
    else:
        backups = {b.stamp: b for b in store.list_backups()}
    expired = select_expired(backups, utcnow(), settings.retention_days, settings.min_keep)
    kept = len(backups) - len(expired)
    if not expired:
        ui.info(
            f"prune: nothing to delete ({len(backups)} backups, retention "
            f"{settings.retention_days} days, keeping at least {settings.min_keep})"
        )
        return 0
    if dry_run:
        ui.table(
            ["would delete", "key"],
            [[s, backups[s].key] for s in expired],
            title="Dry run: nothing deleted",
            plain_lines=[f"would delete  {s}  {backups[s].key}" for s in expired],
        )
        ui.out(f"{len(expired)} would be deleted, {kept} kept (dry run, nothing deleted)")
        return 0
    keys = []
    for stamp in expired:
        ui.info(f"deleting {backups[stamp].key}")
        keys += [backups[stamp].key, manifest_key(backups[stamp].key)]
    with ui.step(f"Deleting {len(expired)} old backups", done=f"Pruned {len(expired)} backups"):
        locked = store.delete(keys)
    for key in locked:
        ui.warn(f"{key}: locked, skipped (a lock or retention rule protects it for now)")
    pruned = len(expired) - sum(1 for k in locked if k.endswith(DUMP_SUFFIX))
    ui.info(f"pruned {pruned} backups older than {settings.retention_days} days, {kept} kept")
    return 0


# --- shared download + decrypt ----------------------------------------------------------


def _check_checksum(path: str, expected: str | None, label: str) -> bool:
    if not expected:
        ui.warn(f"{label} has no sha256 to compare against; skipping the checksum check")
        return False
    actual = sha256_file(path)
    if actual != expected.lower():
        raise CommandError(
            f"checksum mismatch for {label}: the file is corrupt or altered "
            f"(expected sha256 {expected[:12]}..., got {actual[:12]}...)",
            hint="Try another backup with `list`; if several fail, check the bucket's integrity",
        )
    return True


def _check_pgdmp(path: str) -> None:
    if not pg.is_pg_dump(path):
        raise CommandError(
            "the key decrypted the file, but what came out isn't a Postgres dump (no PGDMP header)",
            hint="Was this file made by pgbackup-r2?",
        )


def _fetch_and_decrypt(settings: Settings, store: Store, b: Backup, tmp: str, out: str) -> None:
    """Download `b`, check its checksum, decrypt to `out`, and check it's a pg_dump archive."""
    encrypted = os.path.join(tmp, f"{b.stamp}{DUMP_SUFFIX}")
    with ui.step(
        f"Downloading {b.key}",
        done=f"Downloaded {b.key} ({human_size(b.size)})",
        log_line=f"downloading {b.key} ({human_size(b.size)})",
    ):
        meta = store.download(b.key, encrypted)
    with ui.step("Checking checksum", done="Checksum matches") as s:
        if not _check_checksum(encrypted, meta.get("sha256"), b.key):
            s.done = "No checksum to check"
    _decrypt_step(encrypted, out, settings)
    os.remove(encrypted)


def _decrypt_step(src: str, out: str, settings: Settings) -> None:
    with ui.step("Decrypting", done="Decrypted, and it's a pg_dump archive"):
        agecrypt.decrypt_file(src, out, settings)
        _check_pgdmp(out)


def _load_manifest(settings: Settings, store: Store, b: Backup) -> dict | None:
    with ui.step("Reading the manifest", done="Read the manifest") as s:
        data = store.get_bytes(manifest_key(b.key))
        if data is None:
            s.done = "No manifest found"
            return None
        return json.loads(agecrypt.decrypt_bytes(data, settings))


# --- restore / drill --------------------------------------------------------------------


def _confirm_production(target: str) -> None:
    dbname = parse_url(target).dbname
    if not sys.stdin.isatty():
        raise CommandError(
            "--to-production needs an interactive terminal so you can confirm; "
            "nothing was restored",
            hint="With Docker, run it as `docker run --rm -it ...`",
        )
    print(f"This will overwrite the LIVE database {short_url(target)}.", file=sys.stderr)
    answer = input(f"Type the database name ({dbname}) to continue: ")
    if answer.strip() != dbname:
        raise CommandError(
            "the name didn't match; nothing was restored",
            hint="Run it again and type the name exactly, or restore into a new database "
            "with RESTORE_DATABASE_URL",
        )


def restore(settings: Settings, which: str | None, target: str, *, to_production: bool) -> int:
    parse_url(target)
    if settings.database_url and same_database(target, settings.database_url):
        if not to_production:
            raise CommandError(
                f"refusing to restore into {short_url(target)}: it's the live database "
                "(same as DATABASE_URL)",
                hint="Pass --to-production if you really mean to overwrite it",
            )
        _confirm_production(target)
    agecrypt.require_identity(settings)
    require_tools("age", "pg_restore")
    store = Store(settings)
    b = store.resolve(which)
    with workdir() as tmp:
        plain = os.path.join(tmp, f"{b.stamp}.dump")
        _fetch_and_decrypt(settings, store, b, tmp, plain)
        # The target is named once, in the result below: not in the step lines too.
        with ui.step(
            "Restoring", done="Restored", log_line=f"restoring {b.key} with pg_restore"
        ) as s:
            pg.restore(target, plain)
    ui.result(True, f"{b.key} restored into {short_url(target)}", [f"in {s.seconds:.1f}s"])
    return 0


def drill_preflight(settings: Settings, which: str | None) -> int:
    """The cheap part of a drill: a private key, a reachable bucket, the backup itself.

    `pgb drill` runs this before starting a throwaway Postgres, so a missing key or an empty
    bucket fails in a second, with nothing to clean up. Same checks and messages as drill.
    """
    agecrypt.require_identity(settings)
    b = Store(settings).resolve(which)
    ui.info(f"preflight: {b.key} ({human_size(b.size)}) is ready to drill")
    return 0


def _warn_older_scratch(url: str, dump_path: str) -> None:
    """A dump from a newer pg_dump can carry settings an older server doesn't know (17's
    transaction_timeout broke restores into 16). Warn; the restore itself says if it fails."""
    dumped = pg.dumped_by_major(dump_path)
    try:
        scratch = pg.server_major(url)
    except (PgBackupError, ValueError):
        return  # the restore reports an unreachable scratch database
    if dumped is not None and scratch < dumped:
        fix = (
            f"set PG_MAJOR={dumped} in .env"
            if hints.under_pgb()
            else f"point DRILL_DATABASE_URL at a Postgres {dumped} server"
        )
        ui.warn(
            f"this backup was made by pg_dump {dumped}, but the scratch database is Postgres "
            f"{scratch}: newer dumps can carry settings older servers don't know, so the restore "
            f"may fail. To drill like for like, {fix}"
        )


def drill(settings: Settings, which: str | None) -> int:
    url = settings.require_drill_url()
    if settings.database_url and same_database(url, settings.database_url):
        raise CommandError(
            f"DRILL_DATABASE_URL and DATABASE_URL point at the same database ({short_url(url)})",
            hint="A drill wipes its database: point DRILL_DATABASE_URL at a scratch one",
        )
    agecrypt.require_identity(settings)
    require_tools("age", "pg_restore", "psql")
    store = Store(settings)
    b = store.resolve(which)
    started = time.monotonic()
    drilled_at = utcnow()

    def record(ok: bool, tables: int | None, rows: int | None) -> None:
        _record_drill(store, b, ok, tables, rows, time.monotonic() - started, drilled_at)

    try:
        with workdir() as tmp:
            plain = os.path.join(tmp, f"{b.stamp}.dump")
            _fetch_and_decrypt(settings, store, b, tmp, plain)
            _warn_older_scratch(url, plain)
            with ui.step(
                "Restoring into the scratch database",
                done="Restored into the scratch database",
                log_line=f"restoring {b.key} into {short_url(url)}",
            ) as s:
                pg.restore(url, plain)
            seconds = s.seconds
    except CommandError as exc:
        problem = str(exc) + (f" ({exc.hint})" if exc.hint else "")
        record(False, None, None)
        return _drill_failed(settings, f"{b.key}  ({human_size(b.size)})", [problem])

    head = f"{b.key}  ({human_size(b.size)}, restored in {seconds:.1f}s)"
    manifest = _load_manifest(settings, store, b)
    if manifest is None:
        ui.warn(f"no manifest for {b.key}, so only checking that the dump restores")
        ui.result(
            True, head, ["the dump restores; no manifest found, so row counts weren't checked"]
        )
        record(True, None, None)
        heartbeat.ping(settings.heartbeat_url)
        return 0

    expected = {k: int(v) for k, v in manifest.get("tables", {}).items()}
    with ui.step("Counting restored rows", done="Counted restored rows"):
        actual = pg.table_counts(url)
    report = count_report(expected, actual, settings.drill_tolerance)
    ui.table(
        ["table", "expected", "restored", ""],
        [[t, _n(want), _n(got), "ok" if ok else "MISMATCH"] for t, want, got, ok in report],
        align="lrrl",
        bad_rows=[i for i, row in enumerate(report) if not row[3]],
    )
    problems = compare_counts(expected, actual, settings.drill_tolerance)
    rows = sum(actual.values())
    if problems:
        record(False, len(actual), rows)
        return _drill_failed(settings, head, problems)

    ui.result(True, head, [f"{len(actual)} tables and {rows:,} rows match the manifest"])
    record(True, len(actual), rows)
    heartbeat.ping(settings.heartbeat_url)
    return 0


def _record_drill(
    store: Store,
    b: Backup,
    ok: bool,
    tables: int | None,
    rows: int | None,
    seconds: float,
    drilled_at: datetime,
) -> None:
    """Write <prefix>/.drills/<stamp>.json. Counts only, nothing from the database itself, so
    it isn't encrypted. A failed write is a warning: the drill's own result stands."""
    record = {
        "drilled_at": drilled_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "result": "PASS" if ok else "FAIL",
        "backup": b.key,
        "backup_stamp": b.stamp,
        "tier": b.tier,
        "tables": tables,
        "rows": rows,
        "seconds": round(seconds, 1),
        "tool_version": __version__,
    }
    try:
        key = store.put_drill(make_stamp(drilled_at), record)
    except (ClientError, BotoCoreError) as exc:
        ui.warn(
            f"couldn't record this drill in {store.folder_prefix(DRILLS_FOLDER)} ({exc}); "
            "doctor and list won't see it. The token needs write access there"
        )
    else:
        ui.info(f"recorded the drill in {key}")


def _drill_age(record: dict | None, now: datetime) -> tuple[str, int] | None:
    """('12 days ago', 12) for a drill record, or None."""
    if not record:
        return None
    try:
        when = datetime.fromisoformat(str(record["drilled_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return ago(when, now), (now - when).days


def _last_drill_line(store: Store) -> str:
    last, _ = store.last_drills()
    age = _drill_age(last, utcnow())
    if last is None or age is None:
        return f"Last drill: never. Run {hints.cmd('drill')} to prove a backup restores."
    return f"Last drill: {last.get('result', '?')}, {age[0]}"


def _drill_failed(settings: Settings, head: str, problems: list[str]) -> int:
    ui.result(False, head, problems)
    summary = "\n".join([f"FAIL  {head}", *(f"  {p}" for p in problems)])
    heartbeat.ping(settings.heartbeat_fail_url, failure=summary)
    return 1


# --- decrypt / verify -------------------------------------------------------------------


def decrypt(
    settings: Settings, which: str | None, *, file: str | None, out: str, force: bool
) -> int:
    if os.path.exists(out) and not force:
        raise CommandError(f"{out} already exists", hint="Pass --force to overwrite it")
    out_dir = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(out_dir):
        raise CommandError(
            f"the folder {out_dir} doesn't exist",
            hint="With pgb, write under /out, which is ./out on your machine: "
            "--out /out/backup.dump",
        )
    if not os.access(out_dir, os.W_OK):
        raise CommandError(
            f"can't write to {out_dir} as uid {os.getuid()}",
            hint='On Linux, run the container as yourself: --user "$(id -u):$(id -g)"',
        )
    agecrypt.require_identity(settings)
    require_tools("age")

    partial = out + ".partial"
    try:
        if file:
            _decrypt_local(settings, file, partial)
        else:
            store = Store(settings)
            b = store.resolve(which)
            with workdir() as tmp:
                _fetch_and_decrypt(settings, store, b, tmp, partial)
        os.replace(partial, out)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(partial)

    ui.out(f"wrote {out} ({human_size(os.path.getsize(out))}), a plain pg_dump custom-format file")
    ui.out("restore it with:")
    ui.out(f"  pg_restore --no-owner --no-acl -d <db> {out}")
    return 0


def _sibling_manifest(path: str) -> str | None:
    if not path.endswith(DUMP_SUFFIX):
        return None
    candidate = path.removesuffix(DUMP_SUFFIX) + MANIFEST_SUFFIX
    return candidate if os.path.isfile(candidate) else None


def _decrypt_local(settings: Settings, file: str, out: str) -> dict | None:
    """Decrypt a .dump.age already on disk. Uses a .manifest.age next to it if there is one."""
    if not os.path.isfile(file):
        raise CommandError(
            f"no such file: {file}",
            hint="With pgb, put the file in ./out and give its path as /out/<name>",
        )
    manifest = None
    sibling = _sibling_manifest(file)
    if sibling:
        with (
            ui.step("Reading the manifest next to the file", done="Read the manifest"),
            open(sibling, "rb") as f,
        ):
            manifest = json.loads(agecrypt.decrypt_bytes(f.read(), settings))
        with ui.step("Checking checksum", done="Checksum matches"):
            _check_checksum(file, manifest.get("sha256"), file)
    _decrypt_step(file, out, settings)
    return manifest


def verify(settings: Settings, which: str | None, *, file: str | None) -> int:
    agecrypt.require_identity(settings)
    require_tools("age", "pg_restore")
    with workdir() as tmp:
        plain = os.path.join(tmp, "backup.dump")
        if file:
            label, size = file, os.path.getsize(file) if os.path.isfile(file) else 0
            manifest = _decrypt_local(settings, file, plain)
            checksum = "checksum ok" if manifest else "no manifest next to the file"
        else:
            store = Store(settings)
            b = store.resolve(which)
            label, size = b.key, b.size
            _fetch_and_decrypt(settings, store, b, tmp, plain)
            manifest = _load_manifest(settings, store, b)
            checksum = "checksum ok"
        with ui.step("Reading the table of contents", done="Read the table of contents"):
            toc = pg.list_toc(plain)

    tables = toc_tables(toc)
    counts = {split_qualified(k): int(v) for k, v in (manifest or {}).get("tables", {}).items()}
    version = dumped_from(toc)
    names = [f"{quote_ident(s)}.{quote_ident(t)}" for s, t in tables]
    width = max([len(n) for n in names] + [5])
    plain_lines = [f"  {'table':<{width}}  rows in manifest"] if tables else []
    plain_lines += [
        f"  {name:<{width}}  {_n(counts.get(key))}" for key, name in zip(tables, names, strict=True)
    ]
    summary = f"{len(tables)} tables"
    if manifest is None:
        summary += " (no manifest, so no row counts)"
    else:
        summary += f", {sum(counts.values()):,} rows when the backup was taken"
    readable = "readable pg_dump archive" + (f" from PostgreSQL {version}" if version else "")

    if ui.is_interactive():
        ui.table(
            ["table", "rows in manifest"],
            [[name, _n(counts.get(key))] for key, name in zip(tables, names, strict=True)],
            align="lr",
        )
        ui.result(True, f"{label}  ({human_size(size)})", [f"{checksum}, {readable}", summary])
    else:
        ui.out(f"OK  {label}  ({human_size(size)})")
        ui.out(f"  {checksum}, decrypted, {readable}")
        for line in plain_lines:
            ui.out(line)
        ui.out(f"  {summary}")
    return 0


# --- keygen -----------------------------------------------------------------------------


def keygen() -> int:
    require_tools("age-keygen")
    public, secret = agecrypt.keygen()
    ui.keys(public, secret, KEYGEN_TEMPLATE.format(public=public, secret=secret))
    return 0


# --- schedule ---------------------------------------------------------------------------


def schedule(settings: Settings, at: str | None, *, run_now: bool) -> int:
    times = parse_times(at or settings.backup_at)
    settings.require_database_url()
    agecrypt.check_recipients(settings.age_recipients)
    settings.require_bucket()
    require_tools("psql", "pg_dump", "age")
    ui.info(
        f"pgbackup-r2 {__version__}: backing up {short_url(settings.database_url or '')} "
        f"at {format_times(times)} UTC every day"
    )
    if run_now:
        _scheduled_backup(settings)
    while True:
        target = next_run(utcnow(), times)
        ui.info(f"next backup at {target.strftime('%Y-%m-%d %H:%M UTC')}")
        sleep_until(target)
        _scheduled_backup(settings)


def _scheduled_backup(settings: Settings) -> None:
    """One run inside the scheduler. Any crash is logged and reported, then the loop goes on."""
    try:
        backup(settings)
    except (PgBackupError, BotoCoreError, ClientError) as exc:
        ui.error(f"backup failed: {exc}", getattr(exc, "hint", None))
        heartbeat.ping(settings.heartbeat_fail_url, failure=f"pgbackup-r2 backup failed: {exc}")
    except Exception as exc:
        ui.exception("backup crashed")
        heartbeat.ping(settings.heartbeat_fail_url, failure=f"pgbackup-r2 backup crashed: {exc}")


# --- doctor ------------------------------------------------------------------------------

DOCTOR_REQUIRED = (
    ("S3_BUCKET", "the bucket name"),
    ("AWS_ACCESS_KEY_ID", "the R2/S3 token's access key"),
    ("AWS_SECRET_ACCESS_KEY", "the R2/S3 token's secret"),
    ("DATABASE_URL", "postgresql://user:password@host:5432/dbname"),
    ("AGE_RECIPIENTS", "the age1... public key from keygen"),
)


def _tools_major() -> int | None:
    """The major version of this image's pg_dump, e.g. 16."""
    return pg.client_major()


def doctor(env: Mapping[str, str] | None = None) -> int:
    """Check everything a backup and a drill need, with a fix for each problem. Exit 1 on any ✗."""
    env = os.environ if env is None else env
    failed = 0

    def check(ok: bool | None, label: str, fix: str | None = None) -> None:
        nonlocal failed
        failed += ok is False
        ui.check(ok, label, fix)

    _dotenv_check(env, check)
    missing = [name for name, _ in DOCTOR_REQUIRED if not env.get(name, "").strip()]
    if missing:
        unset = [n for n in missing if n not in env]
        empty = [n for n in missing if n in env]
        said = [
            f"{', '.join(names)} {verb if len(names) == 1 else 'are ' + verb[3:]}"
            for names, verb in ((unset, "is not set"), (empty, "is empty"))
            if names
        ]
        check(
            False,
            f"Settings: {'; '.join(said)}",
            "Fill in "
            + "; ".join(f"{n} ({what})" for n, what in DOCTOR_REQUIRED if n in missing)
            + _start_from(),
        )
    try:
        settings = Settings.from_env(env, require_bucket=False)
    except PgBackupError as exc:
        check(False, f"Settings: {exc}", exc.hint)
        return 1
    path = endpoint_path(settings.s3_endpoint)
    if not missing and not path:
        where = settings.s3_endpoint or "AWS S3"
        check(True, f"Settings: everything a backup needs is set (storage: {where})")
    _typo_checks(env, check)
    if path:
        check(
            False,
            f"Settings: S3_ENDPOINT has a path ({path})",
            "Drop the path; the bucket goes in S3_BUCKET: "
            f"S3_ENDPOINT={endpoint_base(settings.s3_endpoint or '')}",
        )
    if (
        settings.drill_database_url
        and settings.database_url
        and same_database(settings.drill_database_url, settings.database_url)
    ):
        check(
            False,
            "Settings: DRILL_DATABASE_URL and DATABASE_URL point at the same database "
            f"({short_url(settings.database_url)})",
            "A drill wipes its database: point DRILL_DATABASE_URL at a scratch one",
        )
    if not (settings.age_identity or settings.age_identity_file):
        check(
            None,
            "AGE_IDENTITY isn't set: fine for a backup-only server. "
            "verify, drill, decrypt and restore need it",
        )

    # The database: reachable, and the same major version as PG_MAJOR and this image's tools.
    if settings.database_url:
        db = short_url(settings.database_url)
        try:
            num = int(pg.psql(settings.database_url, "SHOW server_version_num"))
            version = pg.server_version(settings.database_url).split()[0]
        except (PgBackupError, ValueError) as exc:
            hint = pg.connection_hint(str(exc), settings.database_url) or (
                "Check DATABASE_URL: postgresql://user:password@host:5432/dbname, "
                "with a direct (not pooled) host"
            )
            check(False, f"Database: can't connect to {db}: {_short(exc)}", hint)
        else:
            major = num // 10000
            want_raw = env.get("PG_MAJOR", "").strip()
            tools = _tools_major()
            want = int(want_raw) if want_raw.isdigit() else tools if hints.under_pgb() else None
            if want is not None and major != want:
                check(
                    False,
                    f"Database: {db} is PostgreSQL {version}, but PG_MAJOR is {want}",
                    f"Set PG_MAJOR={major} in .env, so the image (pg{major}) and the drill's "
                    "scratch database match the server",
                )
            elif tools is not None and major > tools:
                err = pg.newer_server(major, tools)
                check(False, f"Database: {db}: {err}", err.hint)
            elif tools is not None and major != tools and hints.under_pgb():
                check(
                    False,
                    f"Database: {db} is PostgreSQL {version}, but this image's tools are {tools}",
                    f"Use the pg{major} image (pgb: PG_MAJOR={major} in .env)",
                )
            elif tools is not None and major < tools:
                # Run directly, with newer tools: backups work, restoring into a server this
                # old may not.
                where = "this image has pg_dump" if hints.in_image() else "pg_dump here is"
                check(
                    ui.WARN,
                    f"Database: connected to {db}, PostgreSQL {version}, and {where} "
                    f"{tools}: fine for backups. Restoring into a Postgres {major} server may "
                    "not be.",
                    f"Restore and drill into Postgres {tools} or newer, or use Postgres {major}'s "
                    "tools: https://pgbackup-r2.iotabyt.com/concepts/postgres-versions/",
                )
            else:
                check(True, f"Database: connected to {db}, PostgreSQL {version}")

    # The bucket: list the prefix, put a tiny object under it, then delete it again.
    # A path in S3_ENDPOINT has already failed above, and every call would fail the same way.
    bucket_ok = bool(settings.s3_bucket) and not path
    if bucket_ok:
        store = Store(settings)
        bucket_ok = _list_check(store, check)
    if bucket_ok:
        name = f".pgbackup-r2-doctor-{os.urandom(4).hex()}"
        key = f"{store.prefix}/{name}" if store.prefix else name
        try:
            store.put_bytes(key, b"pgbackup-r2 doctor: safe to delete\n")
        except (ClientError, BotoCoreError) as exc:
            check(False, f"Bucket: can't write to {store.where()}: {_short(exc)}", _s3_hint(exc))
        else:
            try:
                locked = store.delete([key])
            except (ClientError, BotoCoreError, CommandError) as exc:
                check(
                    False,
                    f"Bucket: wrote to {store.where()} but can't delete ({_short(exc)})",
                    f"prune needs to delete: give the token Object Read & Write. Remove {key} "
                    "by hand",
                )
            else:
                if locked:
                    check(
                        True,
                        f"Bucket: {store.where()} is writable. The test file {key} is locked by "
                        "a lock or retention rule, so it stays until the lock ends",
                    )
                else:
                    check(
                        True,
                        f"Bucket: {store.where()} is writable (wrote and deleted a test file)",
                    )

    if bucket_ok:
        _layout_checks(settings, store, check)

    # The keys: anything encrypted to AGE_RECIPIENTS must open with AGE_IDENTITY.
    if settings.age_recipients:
        try:
            agecrypt.check_recipients(settings.age_recipients)
        except PgBackupError as exc:
            check(False, f"Keys: {exc}", exc.hint)
        else:
            if settings.age_identity or settings.age_identity_file:
                _check_keys(settings, check)

    # Last: when a backup last restored for real (a warning at most, never a failure).
    if settings.s3_bucket:
        _drill_check(Store(settings), check)

    if failed:
        ui.out(f"\n{failed} {'check' if failed == 1 else 'checks'} failed.")
        return 1
    ui.out("\nAll checks passed.")
    return 0


def _check_keys(settings: Settings, check) -> None:
    probe = b"pgbackup-r2 doctor " + os.urandom(8).hex().encode()
    opened: bytes | None = None
    error: PgBackupError | None = None
    try:
        sealed = agecrypt.encrypt_bytes(probe, settings.age_recipients)
        opened = agecrypt.decrypt_bytes(sealed, settings)
    except PgBackupError as exc:
        error = exc
    if opened == probe:
        check(True, "Keys: AGE_IDENTITY opens what is encrypted to AGE_RECIPIENTS")
        return
    fix = "Make sure AGE_RECIPIENTS and AGE_IDENTITY come from the same `keygen`"
    with contextlib.suppress(Exception), agecrypt.identity_path(settings) as path:
        public = run(["age-keygen", "-y", path], what="age-keygen -y").stdout.decode().split()
        if public:
            fix = f"AGE_IDENTITY's public key is {public[0]}: add it to AGE_RECIPIENTS"
    detail = f": {_short(error)}" if error else ""
    check(False, f"Keys: AGE_IDENTITY can't open backups encrypted to AGE_RECIPIENTS{detail}", fix)


def _start_from() -> str:
    if hints.under_pgb():
        return f". {hints.cmd('init')} writes a commented .env to start from"
    return ". Every setting: https://pgbackup-r2.iotabyt.com/reference/environment/"


def _detect_tail() -> str:
    if hints.under_pgb():
        return f"  ({hints.cmd('init --detect')} writes it into .env)"
    return "  (quotes included)"


_DOTENV_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _dotenv_check(env: Mapping[str, str], check, path: str = ".env") -> None:
    """Without pgb, a .env in this folder does nothing unless the shell loaded it."""
    if hints.under_pgb() or not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            names = [m.group(1) for line in f if (m := _DOTENV_NAME.match(line))]
    except OSError:
        return
    absent = [n for n in dict.fromkeys(names) if n not in env]
    load = "set -a; source .env; set +a"
    if absent:
        shown = ", ".join(absent[:4]) + (f" and {len(absent) - 4} more" if len(absent) > 4 else "")
        check(
            ui.WARN,
            f".env: pgbackup-r2 doesn't read .env, and {shown} from it "
            f"{'isn' if len(absent) == 1 else 'aren'}'t in the environment.",
            f"Load it into this shell first: {load}",
        )
    else:
        check(
            None,
            f".env: pgbackup-r2 doesn't read .env itself; its settings are in the environment "
            f"already ({load} does that)",
        )


def _list_check(store: Store, check) -> bool:
    """List the prefix: what list, restore and drill do first. False if that fails."""
    try:
        backups = store.list_backups()
    except BucketError as exc:
        check(False, f"Bucket: {exc}", exc.hint)
        return False
    except (ClientError, BotoCoreError) as exc:
        check(False, f"Bucket: can't list {store.where()}: {_short(exc)}", _s3_hint(exc))
        return False
    n = len(backups)
    check(True, f"Bucket: listed {store.where()}: {n} backup{'s' if n != 1 else ''}")
    return True


def _short(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= 200 else text[:197] + "..."


def _s3_hint(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code == "NoSuchBucket":
            return "Check S3_BUCKET, and that S3_ENDPOINT is the right account"
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
            return "Check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
        return "Give the token Object Read & Write on this bucket"
    return "Check S3_ENDPOINT (for R2: https://<account-id>.r2.cloudflarestorage.com)"


def _layout_checks(settings: Settings, store: Store, check) -> None:
    """Doctor's view of the bucket layout: tiers, lifecycle rules, files that aren't ours."""
    try:
        rules = store.lifecycle()
        if not settings.tiers:
            layout = store.detect_layout()
            root, foreign = store.folder_stats(None)
            if foreign:
                check(
                    None,
                    f"{foreign} other file{'s' if foreign != 1 else ''} under "
                    f"{store.where()} not made by pgbackup-r2 (left alone)",
                )
            nested = [(store.folder_prefix(f.name), f.backups) for f in layout.folders if f.backups]
            if not root and nested:
                # list, restore and drill look only at the prefix itself without TIERS.
                check(
                    ui.WARN,
                    f"TIERS isn't set and there are no backups directly under {store.where()}, "
                    f"but {nested_line(nested)}.",
                    tiers_hint(nested) + ".",
                )
            elif layout.suggestion:
                check(
                    None,
                    " ".join(layout.describe()),
                    f"Suggested: {'   '.join(layout.suggested_lines())}" + _detect_tail(),
                )
            return
        for tier in settings.tiers:
            backups, foreign = store.folder_stats(tier.folder)
            prefix = store.folder_prefix(tier.folder)
            rule = rule_for(prefix, rules or [])
            life = f", lifecycle: delete after {rule.days} days" if rule else ""
            role = "gets every upload" if tier == settings.tiers[0] else "gets copies"
            n = len(backups)
            check(
                True, f"Tier {tier.name}: {prefix} {role}, {n} backup{'s' if n != 1 else ''}{life}"
            )
            if foreign:
                check(
                    None,
                    f"{foreign} other file{'s' if foreign != 1 else ''} in {tier.folder}/ "
                    "not made by pgbackup-r2 (left alone)",
                )
            if rules is not None and rule is None and not settings.prune:
                check(
                    None,
                    f"{tier.folder}/ has no lifecycle rule that deletes old files, and PRUNE is "
                    "off, so its backups are kept forever",
                    "Add a lifecycle rule for it: https://pgbackup-r2.iotabyt.com/guides/existing-folders/",
                )
        if rules is None:
            check(
                None,
                "Couldn't read the bucket's lifecycle rules (the token may not be allowed to); "
                "check them in your storage dashboard",
            )
    except (ClientError, BotoCoreError, BucketError):
        return  # the bucket check above has already said what's wrong


# --- detect ------------------------------------------------------------------------------


def detect(settings: Settings, *, suggest_only: bool = False) -> int:
    """Look at the folders and lifecycle rules under S3_PREFIX and suggest a TIERS line.

    With --env-lines, the explanation goes to stderr and only the .env lines to stdout, so a
    wrapper like `pgb init --detect` can capture them."""
    store = Store(settings)
    layout: Layout = store.detect_layout()
    lines = layout.describe()
    suggestion = layout.suggested_lines()
    if suggestion:
        if settings.tiers and format_tiers(settings.tiers) == suggestion[0]:
            lines.append(f"Your TIERS already matches: {suggestion[0]}")
            suggestion = []
        else:
            lines.append(f"Suggested: {'   '.join(suggestion)}")
    else:
        lines.append(
            "No tier folders (daily/, monthly/, ...) found. The default fits: backups go "
            "straight under the prefix and are pruned by RETENTION_DAYS."
        )
    if suggest_only:
        for line in lines:
            print(line, file=sys.stderr)
        for line in suggestion:
            print(line)
        return 0
    for line in lines:
        ui.out(line)
    return 0


DRILL_MAX_DAYS = 35


def _drill_check(store: Store, check) -> None:
    """When a backup last restored for real. A warning, never a failure."""
    try:
        last, last_pass = store.last_drills()
    except (ClientError, BotoCoreError, BucketError):
        return
    now = utcnow()
    passed = _drill_age(last_pass, now)
    if passed is None:
        if last is None:
            check(ui.WARN, "This bucket has never been drilled.", f"Run {hints.cmd('drill')}.")
        else:
            failed = _drill_age(last, now)
            when = f", {failed[0]}" if failed else ""
            check(
                ui.WARN,
                f"No drill has passed yet (last drill: {last.get('result', '?')}{when}).",
                f"Fix what the drill reported, then run {hints.cmd('drill')} again.",
            )
        return
    text, days = passed
    if days > DRILL_MAX_DAYS:
        check(
            ui.WARN,
            f"Your last successful drill was {text.removesuffix(' ago')} ago.",
            f"Run {hints.cmd('drill')}.",
        )
    else:
        check(True, f"Last successful drill: {text} ({last_pass.get('backup', '?')})")
    if last is not last_pass and last is not None:
        failed = _drill_age(last, now)
        check(
            ui.WARN,
            f"The most recent drill failed{f' ({failed[0]})' if failed else ''}.",
            f"Run {hints.cmd('drill')} to see why.",
        )


def _typo_checks(env: Mapping[str, str], check) -> None:
    """Warn about settings nobody reads, usually typos: S3_BUKET → S3_BUCKET?

    pgb passes the names in .env (names only, never values) as PGB_ENV_KEYS, so every unknown
    one can be reported. Without pgb, only the process environment exists, which is full of
    other programs' variables: then just the names that look like a misspelt setting count.
    """
    listed = env.get("PGB_ENV_KEYS")
    if listed is not None:
        names, where, suspicious = [n for n in listed.split(",") if n], ".env", False
    else:
        names, where, suspicious = list(env), "Environment", True
    for name, guess in unknown_settings(names, only_suspicious=suspicious):
        if guess:
            check(
                ui.WARN,
                f"{where}: {name} → {guess}? pgbackup-r2 has no setting called {name}.",
                f"Rename it to {guess}.",
            )
        else:
            check(
                ui.WARN,
                f"{where}: {name} isn't a pgbackup-r2 setting, so nothing reads it.",
                "Check the spelling against https://pgbackup-r2.iotabyt.com/reference/environment/,"
                " or remove it if it's not for pgbackup-r2.",
            )
