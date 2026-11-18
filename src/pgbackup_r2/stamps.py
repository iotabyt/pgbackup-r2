"""Backup names. A backup is `<prefix>/<YYYY-MM-DDTHHMMSSZ>.dump.age` plus a `.manifest.age`."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .errors import CommandError

STAMP_FORMAT = "%Y-%m-%dT%H%M%SZ"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")
DUMP_SUFFIX = ".dump.age"
MANIFEST_SUFFIX = ".manifest.age"


def make_stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime(STAMP_FORMAT)


def parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)


def is_stamp(text: str) -> bool:
    if not STAMP_RE.match(text):
        return False
    try:
        parse_stamp(text)
    except ValueError:
        return False
    return True


def normalize_stamp(text: str) -> str:
    """Accept a bare stamp, a file name or a full key, and return the stamp."""
    name = text.strip().rsplit("/", 1)[-1]
    for suffix in (DUMP_SUFFIX, MANIFEST_SUFFIX):
        name = name.removesuffix(suffix)
    if not is_stamp(name):
        raise CommandError(
            f"{text!r} isn't a backup stamp: expected 'latest' or something like "
            "2026-11-18T020000Z",
            hint="Run `pgb list` to see the backups and their stamps",
        )
    return name


def dump_key(prefix: str, stamp: str) -> str:
    prefix = prefix.strip("/")
    name = f"{stamp}{DUMP_SUFFIX}"
    return f"{prefix}/{name}" if prefix else name


def manifest_key(dump_key: str) -> str:
    """labpadi/2026-11-18T020000Z.dump.age -> labpadi/2026-11-18T020000Z.manifest.age"""
    if not dump_key.endswith(DUMP_SUFFIX):
        raise ValueError(f"not a dump key: {dump_key}")
    return dump_key.removesuffix(DUMP_SUFFIX) + MANIFEST_SUFFIX
