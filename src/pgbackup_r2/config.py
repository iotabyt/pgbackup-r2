"""Settings come from environment variables only (a .env file read by Docker, usually)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import ConfigError
from .pgurl import parse_url


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def _tolerance(env: Mapping[str, str]) -> float:
    raw = _get(env, "DRILL_TOLERANCE")
    if raw is None:
        return 0.01
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"DRILL_TOLERANCE must be a number like 0.01, got {raw!r}") from None
    if not 0 <= value < 1:
        raise ConfigError(f"DRILL_TOLERANCE must be between 0 and 1 (0.01 = 1%), got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(default=None, repr=False)
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint: str | None = None
    s3_region: str = "auto"
    age_recipients: tuple[str, ...] = ()
    age_identity: str | None = field(default=None, repr=False)
    age_identity_file: str | None = None
    drill_database_url: str | None = field(default=None, repr=False)
    restore_database_url: str | None = field(default=None, repr=False)
    retention_days: int = 30
    min_keep: int = 7
    backup_at: str = "02:00"
    heartbeat_url: str | None = field(default=None, repr=False)
    heartbeat_fail_url: str | None = field(default=None, repr=False)
    drill_tolerance: float = 0.01

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, require_bucket: bool = True
    ) -> Settings:
        env = os.environ if env is None else env

        bucket = _get(env, "S3_BUCKET")
        if require_bucket and not bucket:
            raise ConfigError("S3_BUCKET is not set", hint="Put the bucket name in your .env")

        identity = _get(env, "AGE_IDENTITY")
        identity_file = _get(env, "AGE_IDENTITY_FILE")
        if identity and identity_file:
            raise ConfigError(
                "both AGE_IDENTITY and AGE_IDENTITY_FILE are set: keep one and remove the other"
            )

        recipients = tuple(
            r.strip() for r in (_get(env, "AGE_RECIPIENTS") or "").split(",") if r.strip()
        )

        database_url = _get(env, "DATABASE_URL")
        drill_url = _get(env, "DRILL_DATABASE_URL")
        restore_url = _get(env, "RESTORE_DATABASE_URL")
        for url in (database_url, drill_url, restore_url):
            if url:
                parse_url(url)  # fail early with a readable message

        return cls(
            database_url=database_url,
            s3_bucket=bucket,
            s3_prefix=(_get(env, "S3_PREFIX") or "").strip("/"),
            s3_endpoint=_get(env, "S3_ENDPOINT"),
            s3_region=_get(env, "S3_REGION") or "auto",
            age_recipients=recipients,
            age_identity=identity,
            age_identity_file=identity_file,
            drill_database_url=drill_url,
            restore_database_url=restore_url,
            retention_days=_int(env, "RETENTION_DAYS", 30, minimum=0),
            min_keep=_int(env, "MIN_KEEP", 7, minimum=1),
            backup_at=_get(env, "BACKUP_AT") or "02:00",
            heartbeat_url=_get(env, "HEARTBEAT_URL"),
            heartbeat_fail_url=_get(env, "HEARTBEAT_FAIL_URL"),
            drill_tolerance=_tolerance(env),
        )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigError(
                "DATABASE_URL is not set",
                hint="Set it to the postgres:// URL of the database to back up",
            )
        return self.database_url

    def require_drill_url(self) -> str:
        if not self.drill_database_url:
            raise ConfigError(
                "DRILL_DATABASE_URL is not set",
                hint="Point it at a scratch database the drill can wipe",
            )
        return self.drill_database_url

    def restore_target(self, target: str | None) -> str:
        """--target if given, else RESTORE_DATABASE_URL. Prefer the env var: a URL on the
        command line, password included, is visible to anyone who can run `ps`."""
        url = target or self.restore_database_url
        if not url:
            raise ConfigError(
                "no restore target: RESTORE_DATABASE_URL is not set and --target wasn't given",
                hint="Set RESTORE_DATABASE_URL in your .env to the postgres:// URL to restore into",
            )
        return url

    def require_bucket(self) -> str:
        if not self.s3_bucket:
            raise ConfigError("S3_BUCKET is not set", hint="Put the bucket name in your .env")
        return self.s3_bucket
