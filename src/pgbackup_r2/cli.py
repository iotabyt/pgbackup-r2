"""pgbackup-r2 command line."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Sequence

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)
from rich_argparse import RawDescriptionRichHelpFormatter

from . import __version__, commands, heartbeat, hints, pg, ui
from .config import DEFAULTS, Settings
from .errors import CommandError, PgBackupError

PINGS_ON_FAILURE = ("backup", "drill")


class _HelpParser(argparse.ArgumentParser):
    """Help in the order people read it: what the command does, how to call it, examples."""

    def format_help(self) -> str:
        formatter = self._get_formatter()
        formatter.add_text(self.description)
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        for group in self._action_groups:
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(group._group_actions)
            formatter.end_section()
        formatter.add_text(self.epilog)
        return formatter.format_help()


def _prog() -> str:
    """'pgb' when run through the pgb wrapper, else 'pgbackup-r2'."""
    return hints.prog()


def _examples(*lines: tuple[str, str]) -> str:
    width = max(len(cmd) for cmd, _ in lines) + 2
    body = "\n".join(
        f"  {cmd:<{width}}{('# ' + note) if note else ''}".rstrip() for cmd, note in lines
    )
    return "Examples:\n" + body


KEY_NEED = ("AGE_IDENTITY", "the private key (AGE-SECRET-KEY-1...), or AGE_IDENTITY_FILE: a file")
BUCKET_NEED = ("S3_BUCKET", "and the rest of the bucket settings, as for backup")


def _needs(*lines: tuple[str, str]) -> str:
    """What a command needs set, for its help: the part pgb otherwise handles for you."""
    width = max(len(name) for name, _ in lines) + 2
    return "\n\nNeeds:\n" + "\n".join(f"  {name:<{width}}{what}" for name, what in lines)


def build_parser() -> argparse.ArgumentParser:
    prog = _prog()
    d = DEFAULTS
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="warnings only"
    )
    common.add_argument(
        "--plain",
        action="store_true",
        default=argparse.SUPPRESS,
        help="plain log lines: no colour, spinners or tables (automatic when not a terminal)",
    )

    fmt = RawDescriptionRichHelpFormatter
    parser = _HelpParser(
        prog=prog,
        description="Encrypted Postgres backups to R2 or any S3, with restore drills that prove "
        "they work.\nSettings come from environment variables; it doesn't read .env itself.\n"
        "pgb hands .env to Docker. Without pgb, load it first: set -a; source .env; set +a",
        epilog=_examples(
            (f"{prog} doctor", "check everything is set up"),
            (f"{prog} backup", "back up now"),
            (f"{prog} drill", "prove the latest backup restores"),
        )
        + "\n\nDocs: https://pgbackup-r2.iotabyt.com",
        parents=[common],
        formatter_class=fmt,
    )
    parser.add_argument("--version", action="version", version=f"pgbackup-r2 {__version__}")
    sub = parser.add_subparsers(
        dest="command", metavar="COMMAND", required=True, parser_class=_HelpParser
    )

    def add(
        name: str, summary: str, *examples: tuple[str, str], needs: str = ""
    ) -> argparse.ArgumentParser:
        return sub.add_parser(
            name,
            parents=[common],
            help=(first := summary.split(". ")[0].rstrip("."))[0].lower() + first[1:],
            description=summary + needs,
            epilog=_examples(*((f"{prog} {cmd}", note) for cmd, note in examples)),
            formatter_class=fmt,
        )

    p = add(
        "backup",
        "Take one encrypted backup now.",
        ("backup", ""),
        ("backup --no-prune", "keep old backups this time"),
    )
    p.add_argument("--no-prune", action="store_true", help="don't prune old backups afterwards")

    add(
        "list",
        "List the backups, oldest first, and when the last drill ran.",
        ("list", ""),
        ("list --plain", "one line per backup, for scripts"),
    )

    p = add(
        "prune",
        f"Delete backups older than RETENTION_DAYS ({d['RETENTION_DAYS']}), always keeping the "
        f"newest MIN_KEEP ({d['MIN_KEEP']}).",
        ("prune --dry-run", "show what would go"),
        ("prune", ""),
    )
    p.add_argument("--dry-run", action="store_true", help="show what would be deleted")

    p = add(
        "restore",
        "Restore a backup into RESTORE_DATABASE_URL.",
        ("restore", "the latest backup"),
        ("restore 2026-11-18T020000Z", "a specific one"),
        ("restore latest --to-production", "over the live database; asks you to confirm"),
        needs=_needs(
            ("RESTORE_DATABASE_URL", "the database to restore into (or --target URL)"),
            KEY_NEED,
            BUCKET_NEED,
            ("On PATH", "age and pg_restore (as new as the backup's pg_dump, or newer)"),
        ),
    )
    p.add_argument(
        "stamp",
        nargs="?",
        default="latest",
        metavar="STAMP|latest",
        help="latest (the default), a stamp like 2026-11-18T020000Z, or <tier>/<stamp>",
    )
    p.add_argument(
        "--target",
        metavar="URL",
        help="postgres:// URL to restore into (default: $RESTORE_DATABASE_URL, which keeps "
        "the password out of the process list)",
    )
    p.add_argument(
        "--to-production",
        action="store_true",
        help="allow restoring over DATABASE_URL (asks you to type the database name)",
    )

    p = add(
        "drill",
        "Restore a backup into a throwaway database and check every table.",
        ("drill", "the latest backup"),
        ("drill 2026-11-18T020000Z", "a specific one"),
        ("drill monthly/2026-11-01T020000Z", "with TIERS: one in a given tier"),
        needs=_needs(
            ("DRILL_DATABASE_URL", "a scratch database the drill may wipe, never DATABASE_URL's"),
            KEY_NEED,
            BUCKET_NEED,
            ("On PATH", "age, pg_restore and psql (as new as the backup's pg_dump, or newer)"),
        )
        + "\n\npgb drill starts a throwaway Postgres for DRILL_DATABASE_URL; without pgb, bring "
        "your own.",
    )
    p.add_argument(
        "stamp",
        nargs="?",
        default="latest",
        metavar="STAMP|latest",
        help="latest (the default), a stamp like 2026-11-18T020000Z, or <tier>/<stamp>",
    )
    p.add_argument(
        "--preflight",
        action="store_true",
        help="only check the private key, the bucket and that the backup exists; restores "
        "nothing and needs no database (pgb drill runs this before starting Postgres)",
    )

    add(
        "keygen",
        "Print a new key pair. Nothing is written to disk.",
        ("keygen", ""),
        ("keygen --plain", "exactly as the lines go in .env"),
    )

    add(
        "doctor",
        "Check the settings, the database, the bucket, the keys and the last drill.",
        ("doctor", ""),
    )

    p = add(
        "detect",
        "Look at the bucket's folders and lifecycle rules, and suggest TIERS.",
        ("detect", ""),
        ("detect --env-lines", "only the suggested .env lines, for scripts"),
    )
    p.add_argument(
        "--env-lines",
        action="store_true",
        help="print only the suggested .env lines on stdout (the explanation goes to stderr)",
    )

    p = add(
        "decrypt",
        "Write a backup out as a plain pg_dump file.",
        ("decrypt latest --out /out/backup.dump", ""),
        ("decrypt --file /out/x.dump.age --out /out/x.dump", "a file you downloaded"),
        needs=_needs(
            KEY_NEED,
            ("S3_BUCKET", "and the rest of the bucket settings (not with --file)"),
            ("On PATH", "age"),
        ),
    )
    p.add_argument(
        "stamp",
        nargs="?",
        default=None,
        metavar="STAMP|latest",
        help="latest (the default), a stamp like 2026-11-18T020000Z, or <tier>/<stamp>",
    )
    p.add_argument("--file", metavar="PATH", help="decrypt a .dump.age on disk instead (no S3)")
    p.add_argument("--out", required=True, metavar="PATH", help="where to write the dump")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")

    p = add(
        "verify",
        "Check a backup downloads, decrypts and is a real dump. No database needed.",
        ("verify", "the latest backup"),
        ("verify 2026-11-18T020000Z", "a specific one"),
        ("verify --file /out/2026-11-18T020000Z.dump.age", "a file you downloaded"),
        needs=_needs(
            KEY_NEED,
            ("S3_BUCKET", "and the rest of the bucket settings (not with --file)"),
            ("On PATH", "age and pg_restore"),
        ),
    )
    p.add_argument(
        "stamp",
        nargs="?",
        default=None,
        metavar="STAMP|latest",
        help="latest (the default), a stamp like 2026-11-18T020000Z, or <tier>/<stamp>",
    )
    p.add_argument("--file", metavar="PATH", help="verify a .dump.age on disk instead (no S3)")

    p = add(
        "schedule",
        f"Stay running and back up every day at BACKUP_AT ({d['BACKUP_AT']} UTC). "
        "The container's default command.",
        ("schedule", ""),
        ("schedule --at 02:00,14:00 --run-now", "twice a day, and once right now"),
    )
    p.add_argument(
        "--at",
        metavar="HH:MM[,HH:MM]",
        help=f"UTC times (default: $BACKUP_AT or {d['BACKUP_AT']})",
    )
    p.add_argument("--run-now", action="store_true", help="also take a backup straight away")
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "keygen":
        return commands.keygen()
    if cmd == "doctor":
        return commands.doctor()

    file_mode = cmd in ("decrypt", "verify") and bool(args.file)
    if file_mode and args.stamp:
        raise PgBackupError(
            "give either a STAMP or --file, not both",
            hint=f"`{cmd} STAMP` reads from the bucket; `{cmd} --file PATH` reads a local file",
        )
    settings = Settings.from_env(require_bucket=not file_mode)

    if cmd == "detect":
        return commands.detect(settings, suggest_only=args.env_lines)
    if cmd == "backup":
        commands.backup(settings, prune=False if args.no_prune else None)
        return 0
    if cmd == "list":
        return commands.list_backups(settings)
    if cmd == "prune":
        return commands.prune_backups(settings, dry_run=args.dry_run)
    if cmd == "restore":
        target = settings.restore_target(args.target)
        return commands.restore(settings, args.stamp, target, to_production=args.to_production)
    if cmd == "drill":
        if args.preflight:
            return commands.drill_preflight(settings, args.stamp)
        return commands.drill(settings, args.stamp)
    if cmd == "decrypt":
        return commands.decrypt(
            settings, args.stamp or "latest", file=args.file, out=args.out, force=args.force
        )
    if cmd == "verify":
        return commands.verify(settings, args.stamp or "latest", file=args.file)
    if cmd == "schedule":
        return commands.schedule(settings, args.at, run_now=args.run_now)
    raise AssertionError(cmd)


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or type(exc).__name__


def _hint(exc: BaseException) -> str | None:
    """A short fix for the error, if we know one."""
    if isinstance(exc, PgBackupError):
        if exc.hint is None and isinstance(exc, CommandError):
            return pg.connection_hint(str(exc), os.environ.get("DATABASE_URL")) or hints.doctor()
        return exc.hint or hints.doctor()
    if isinstance(exc, NoCredentialsError):
        return "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env"
    if isinstance(exc, EndpointConnectionError):
        return "Check S3_ENDPOINT (for R2: https://<account-id>.r2.cloudflarestorage.com)"
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code == "NoSuchBucket":
            return "Check S3_BUCKET, and that S3_ENDPOINT is the right account"
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "403"):
            return (
                "Check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, and that the token has "
                "Object Read & Write on this bucket"
            )
    # Every error ends with something to do next.
    return hints.doctor()


def _on_sigterm(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui.configure(
        plain=getattr(args, "plain", False),
        quiet=getattr(args, "quiet", False),
        command=args.command,
    )
    # As PID 1 in a container, SIGTERM is ignored unless we handle it.
    if os.getpid() == 1:
        signal.signal(signal.SIGTERM, _on_sigterm)

    def report_failure(message: str) -> None:
        if args.command in PINGS_ON_FAILURE:
            heartbeat.ping(
                os.environ.get("HEARTBEAT_FAIL_URL"),
                failure=f"pgbackup-r2 {args.command} failed: {message}",
            )

    try:
        return _dispatch(args)
    except (PgBackupError, BotoCoreError, ClientError) as exc:
        message = _one_line(exc)
        ui.error(message, _hint(exc))
        report_failure(message)
        return 1
    except KeyboardInterrupt:
        ui.info("stopped")
        return 0 if args.command == "schedule" else 130
    except Exception as exc:
        ui.exception("unexpected error (this is a bug, please report it)")
        report_failure(_one_line(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
