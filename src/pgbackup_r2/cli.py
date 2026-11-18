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
from rich_argparse import RichHelpFormatter

from . import __version__, commands, heartbeat, ui
from .config import Settings
from .errors import PgBackupError

PINGS_ON_FAILURE = ("backup", "drill")


def build_parser() -> argparse.ArgumentParser:
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

    fmt = RichHelpFormatter
    parser = argparse.ArgumentParser(
        prog="pgbackup-r2",
        description="Encrypted Postgres backups to R2/S3, with restore drills. "
        "Settings come from environment variables (usually a .env file).",
        parents=[common],
        formatter_class=fmt,
    )
    parser.add_argument("--version", action="version", version=f"pgbackup-r2 {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(
            name, parents=[common], help=help, description=help, formatter_class=fmt
        )

    p = add("backup", "take one encrypted backup now")
    p.add_argument("--no-prune", action="store_true", help="don't prune old backups afterwards")

    add("list", "list backups, oldest first")

    p = add("prune", "delete backups older than RETENTION_DAYS (always keeps MIN_KEEP)")
    p.add_argument("--dry-run", action="store_true", help="show what would be deleted")

    p = add("restore", "restore a backup into a database")
    p.add_argument("stamp", nargs="?", default="latest", metavar="STAMP|latest")
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

    p = add("drill", "restore into DRILL_DATABASE_URL and check every table's row count")
    p.add_argument("stamp", nargs="?", default="latest", metavar="STAMP|latest")

    add("keygen", "print a new age key pair (writes nothing to disk)")

    p = add("decrypt", "write a backup out as a plain pg_dump file")
    p.add_argument("stamp", nargs="?", default=None, metavar="STAMP|latest")
    p.add_argument("--file", metavar="PATH", help="decrypt a .dump.age on disk instead (no S3)")
    p.add_argument("--out", required=True, metavar="PATH", help="where to write the dump")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")

    p = add("verify", "check a backup opens and is a real dump, without a database")
    p.add_argument("stamp", nargs="?", default=None, metavar="STAMP|latest")
    p.add_argument("--file", metavar="PATH", help="verify a .dump.age on disk instead (no S3)")

    p = add("schedule", "stay running and back up at set UTC times (the container default)")
    p.add_argument("--at", metavar="HH:MM[,HH:MM]", help="UTC times (default: $BACKUP_AT or 02:00)")
    p.add_argument("--run-now", action="store_true", help="also take a backup straight away")
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "keygen":
        return commands.keygen()

    file_mode = cmd in ("decrypt", "verify") and bool(args.file)
    if file_mode and args.stamp:
        raise PgBackupError("give either a STAMP or --file, not both")
    settings = Settings.from_env(require_bucket=not file_mode)

    if cmd == "backup":
        commands.backup(settings, prune=not args.no_prune)
        return 0
    if cmd == "list":
        return commands.list_backups(settings)
    if cmd == "prune":
        return commands.prune_backups(settings, dry_run=args.dry_run)
    if cmd == "restore":
        target = settings.restore_target(args.target)
        return commands.restore(settings, args.stamp, target, to_production=args.to_production)
    if cmd == "drill":
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
        return exc.hint
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
    return None


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
