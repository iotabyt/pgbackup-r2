"""Small helpers: running tools, temp dirs, sizes, checksums."""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime

from .errors import CommandError

TOOL_HINT = (
    "Use the Docker image (ghcr.io/iotabyt/pgbackup-r2), which has everything installed, "
    "or install the missing tool so it's on PATH."
)


def require_tools(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise CommandError(f"{', '.join(missing)} not found on PATH", hint=TOOL_HINT)


def stderr_tail(stderr: bytes | str | None, lines: int = 3) -> str:
    """The last few non-empty lines of a tool's stderr, joined into one line."""
    if not stderr:
        return "no error output"
    text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return "; ".join(kept[-lines:]) or "no error output"


def run(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    input: bytes | None = None,
    what: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a tool, capture its output, and turn a non-zero exit into a CommandError."""
    proc = subprocess.run(list(args), env=env, input=input, capture_output=True, check=False)
    if proc.returncode != 0:
        name = what or args[0]
        raise CommandError(f"{name} failed (exit {proc.returncode}): {stderr_tail(proc.stderr)}")
    return proc


@contextlib.contextmanager
def workdir() -> Iterator[str]:
    """A private (0700) temporary directory, removed afterwards whatever happens."""
    path = tempfile.mkdtemp(prefix="pgbackup-r2-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(n: int | float) -> str:
    """1000-based sizes, as in '48.2 MB'."""
    if n < 1000:
        return f"{int(n)} B"
    for unit in ("kB", "MB", "GB", "TB"):
        n /= 1000
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}"
    raise AssertionError("unreachable")


def ago(then: datetime, now: datetime) -> str:
    """'just now', '6 hours ago', '3 days ago'."""
    seconds = int((now - then).total_seconds())
    if seconds < 0:
        return "in the future"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
