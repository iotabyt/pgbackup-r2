"""Hints that name a command name the one the user actually typed: pgb or pgbackup-r2.

The pgb wrappers (scripts/pgb, scripts/pgb.ps1) pass PGB_VERSION into every container they
start. It's internal: nobody sets it by hand. When it's there, hints say `pgb doctor`;
when it isn't, the CLI was run directly and hints say `pgbackup-r2 doctor`.
"""

from __future__ import annotations

import os

WRAPPER_ENV = "PGB_VERSION"
# Set in the Docker image (Dockerfile), e.g. pg19: the tools came with the image, not from
# this machine's PATH. Also internal.
IMAGE_ENV = "PGBACKUP_R2_IMAGE"


def under_pgb() -> bool:
    return bool(os.environ.get(WRAPPER_ENV))


def in_image() -> bool:
    return bool(os.environ.get(IMAGE_ENV))


def prog() -> str:
    return "pgb" if under_pgb() else "pgbackup-r2"


def cmd(args: str) -> str:
    """A command to run, in backticks: cmd("drill") -> `pgb drill` or `pgbackup-r2 drill`."""
    return f"`{prog()} {args}`"


def doctor() -> str:
    return f"Run {cmd('doctor')}: it checks the settings, the database, the bucket and the keys"
