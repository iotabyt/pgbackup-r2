"""The commands. Each returns an exit code or raises a PgBackupError / boto error.

All output goes through `ui`, which decides between interactive and plain mode.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, agecrypt, heartbeat, pg, ui
from .checks import (
    compare_counts,
    count_report,
    dumped_from,
    quote_ident,
    split_qualified,
    toc_tables,
)
from .config import Settings
from .errors import CommandError, PgBackupError
from .pgurl import parse_url, redact, same_database
from .retention import select_expired
from .scheduling import format_times, next_run, parse_times, sleep_until
from .stamps import DUMP_SUFFIX, MANIFEST_SUFFIX, make_stamp, manifest_key
from .storage import Backup, Store
from .util import ago, human_size, require_tools, sha256_file, workdir

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


def backup(settings: Settings, *, prune: bool = True) -> str:
    """Take one encrypted backup and upload it with its manifest. Returns the dump key."""
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
        log_line=f"backing up {redact(url)} to s3://{store.bucket}/{key}",
    ) as s:
        version = pg.server_version(url)
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
    if prune:
        prune_backups(settings, store=store)
    heartbeat.ping(settings.heartbeat_url)
    return key


# --- list / prune -----------------------------------------------------------------------


def list_backups(settings: Settings) -> int:
    store = Store(settings)
    with ui.step(f"Listing {store.where()}", done=f"Listed {store.where()}"):
        backups = store.list_backups()
    if not backups:
        ui.out("no backups yet")
        return 0
    now = utcnow()
    width = max(len(human_size(b.size)) for b in backups)
    ui.table(
        ["stamp", "age", "size", "key"],
        [[b.stamp, ago(b.taken_at, now), human_size(b.size), b.key] for b in backups],
        align="llrl",
        title=f"{len(backups)} backup{'s' if len(backups) != 1 else ''} in {store.where()}",
        plain_lines=[f"{b.stamp}  {human_size(b.size):>{width}}  {b.key}" for b in backups],
    )
    return 0


def prune_backups(settings: Settings, *, dry_run: bool = False, store: Store | None = None) -> int:
    store = store or Store(settings)
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
        store.delete(keys)
    ui.info(f"pruned {len(expired)} backups older than {settings.retention_days} days, {kept} kept")
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
    print(f"This will overwrite the LIVE database {redact(target)}.", file=sys.stderr)
    answer = input(f"Type the database name ({dbname}) to continue: ")
    if answer.strip() != dbname:
        raise CommandError("the name didn't match; nothing was restored")


def restore(settings: Settings, which: str | None, target: str, *, to_production: bool) -> int:
    parse_url(target)
    if settings.database_url and same_database(target, settings.database_url):
        if not to_production:
            raise CommandError(
                f"refusing to restore into {redact(target)}: it's the live database "
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
        with ui.step(
            f"Restoring into {redact(target)}",
            done=f"Restored into {redact(target)}",
            log_line=f"restoring {b.key} into {redact(target)}",
        ) as s:
            pg.restore(target, plain)
    ui.info(f"restored {b.key} into {redact(target)} in {s.seconds:.1f}s")
    if ui.is_interactive():
        ui.result(True, f"{b.key} restored into {redact(target)}")
    return 0


def drill(settings: Settings, which: str | None) -> int:
    url = settings.require_drill_url()
    if settings.database_url and same_database(url, settings.database_url):
        raise CommandError(
            f"refusing to drill: DRILL_DATABASE_URL ({redact(url)}) is the live database",
            hint="Point DRILL_DATABASE_URL at a scratch database the drill can wipe",
        )
    agecrypt.require_identity(settings)
    require_tools("age", "pg_restore", "psql")
    store = Store(settings)
    b = store.resolve(which)

    try:
        with workdir() as tmp:
            plain = os.path.join(tmp, f"{b.stamp}.dump")
            _fetch_and_decrypt(settings, store, b, tmp, plain)
            with ui.step(
                "Restoring into the scratch database",
                done=f"Restored into {redact(url)}",
                log_line=f"restoring {b.key} into {redact(url)}",
            ) as s:
                pg.restore(url, plain)
            seconds = s.seconds
    except CommandError as exc:
        problem = str(exc) + (f" ({exc.hint})" if exc.hint else "")
        return _drill_failed(settings, f"{b.key}  ({human_size(b.size)})", [problem])

    head = f"{b.key}  ({human_size(b.size)}, restored in {seconds:.1f}s)"
    manifest = _load_manifest(settings, store, b)
    if manifest is None:
        ui.warn(f"no manifest for {b.key}, so only checking that the dump restores")
        ui.result(
            True, head, ["the dump restores; no manifest found, so row counts weren't checked"]
        )
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
    if problems:
        return _drill_failed(settings, head, problems)

    rows = sum(actual.values())
    ui.result(True, head, [f"{len(actual)} tables and {rows:,} rows match the manifest"])
    heartbeat.ping(settings.heartbeat_url)
    return 0


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
        raise CommandError(f"the folder {out_dir} doesn't exist")
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
        raise CommandError(f"no such file: {file}")
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
        f"pgbackup-r2 {__version__}: backing up {redact(settings.database_url or '')} "
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
