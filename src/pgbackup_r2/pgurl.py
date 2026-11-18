"""Turn postgres:// URLs into libpq environment variables.

The URL never goes on a command line: the password would show up in `ps`.
Instead every subprocess gets PGHOST, PGPASSWORD and friends in its environment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .errors import ConfigError

SCHEMES = ("postgres", "postgresql")
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5432
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class PgTarget:
    dbname: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    sslmode: str | None = None


def parse_url(url: str) -> PgTarget:
    """Parse a postgres:// or postgresql:// URL. Raises ConfigError on anything else."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ConfigError(f"not a valid Postgres URL: {redact(url)} ({exc})") from None
    if parts.scheme not in SCHEMES:
        shown = parts.scheme or "none"
        raise ConfigError(
            f"unsupported URL scheme '{shown}' in {redact(url)}: use postgres:// or postgresql://"
        )
    dbname = unquote(parts.path.lstrip("/"))
    if not dbname:
        raise ConfigError(
            f"{redact(url)} has no database name "
            "(expected postgres://user:password@host:5432/dbname)"
        )
    sslmode = parse_qs(parts.query).get("sslmode", [None])[-1]
    return PgTarget(
        dbname=dbname,
        host=unquote(parts.hostname) if parts.hostname else None,
        port=port,
        user=unquote(parts.username) if parts.username else None,
        password=unquote(parts.password) if parts.password is not None else None,
        sslmode=sslmode,
    )


def pg_vars(url: str) -> dict[str, str]:
    """The PG* variables libpq needs to connect to `url`."""
    t = parse_url(url)
    env = {"PGDATABASE": t.dbname}
    if t.host:
        env["PGHOST"] = t.host
    if t.port:
        env["PGPORT"] = str(t.port)
    if t.user:
        env["PGUSER"] = t.user
    if t.password is not None:
        env["PGPASSWORD"] = t.password
    if t.sslmode:
        env["PGSSLMODE"] = t.sslmode
    return env


def child_env(url: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for a pg_dump/pg_restore/psql child: inherited PG* removed, then ours added."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.startswith("PG")}
    env.update(pg_vars(url))
    env["PGAPPNAME"] = "pgbackup-r2"
    return env


def _identity(url: str) -> tuple[str, int, str]:
    t = parse_url(url)
    host = (t.host or DEFAULT_HOST).lower()
    if host in _LOCAL_HOSTS:
        host = DEFAULT_HOST
    return host, t.port or DEFAULT_PORT, t.dbname


def same_database(a: str, b: str) -> bool:
    """True when both URLs point at the same host, port and database name."""
    return _identity(a) == _identity(b)


_QUERY_PASSWORD = re.compile(r"(?i)(password=)[^&]*")


def redact(url: str) -> str:
    """Hide credentials so a URL can be logged: postgres://***@host/db."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rpartition("@")[2]
    query = _QUERY_PASSWORD.sub(r"\1***", parts.query)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
