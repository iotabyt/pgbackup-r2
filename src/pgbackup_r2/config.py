"""Settings come from environment variables only. pgbackup-r2 never reads a .env file itself:
pgb hands .env to Docker, and without pgb you load it into the shell first
(set -a; source .env; set +a)."""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import ConfigError
from .pgurl import parse_url
from .tiers import Tier, parse_tiers

# The defaults, in one place. config.py, pgb init's .env template, the docs reference and
# --help all show these; tests/test_defaults.py checks that they agree.
DEFAULTS: dict[str, str] = {
    "PG_MAJOR": "16",
    "S3_REGION": "auto",
    "BACKUP_AT": "02:00",
    "RETENTION_DAYS": "30",
    "MIN_KEEP": "7",
    "DRILL_TOLERANCE": "0.01",
}

# Every setting pgbackup-r2 and pgb read. doctor uses this to catch typos in .env.
KNOWN_SETTINGS: tuple[str, ...] = (
    # needed to back up
    "PG_MAJOR",
    "DATABASE_URL",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AGE_RECIPIENTS",
    # optional
    "S3_PREFIX",
    "S3_REGION",
    "AGE_IDENTITY",
    "AGE_IDENTITY_FILE",
    "BACKUP_AT",
    "RETENTION_DAYS",
    "MIN_KEEP",
    "PRUNE",
    "TIERS",
    "HEARTBEAT_URL",
    "HEARTBEAT_FAIL_URL",
    "RESTORE_DATABASE_URL",
    "DRILL_DATABASE_URL",
    "DRILL_TOLERANCE",
    "PGB_NETWORK",
    "PGB_IMAGE",
    "NO_COLOR",
    # set by pgb for the container; not for people to set
    "PGB_VERSION",
    "PGB_ENV_KEYS",
    # set in the Docker image (pg16 ... pg19); not for people to set
    "PGBACKUP_R2_IMAGE",
)
# Prefixes that are ours: an unknown name starting with one of these is almost surely a typo.
_OUR_PREFIXES = ("S3_", "AGE_", "PGB_", "DRILL_", "HEARTBEAT_", "BACKUP_", "RESTORE_", "RETENTION")


def unknown_settings(
    names: Iterable[str], *, only_suspicious: bool = False
) -> list[tuple[str, str | None]]:
    """(name, did-you-mean) for each name pgbackup-r2 doesn't know.

    `names` from .env are all checked. With only_suspicious (names from a whole process
    environment, where most variables belong to something else), only names that look like a
    misspelt setting or start with one of our prefixes are reported. AWS_* names belong to
    boto3, which reads several more than the two keys, so they're never reported.
    """
    found = []
    for name in sorted(set(names)):
        if name in KNOWN_SETTINGS or name.startswith("AWS_"):
            continue
        close = difflib.get_close_matches(name, KNOWN_SETTINGS, n=1, cutoff=0.8)
        guess = close[0] if close else None
        if only_suspicious and guess is None and not name.startswith(_OUR_PREFIXES):
            continue
        found.append((name, guess))
    return found


def _fix_hint(name: str) -> str:
    default = DEFAULTS.get(name)
    return f"Fix {name} in .env" + (f" (the default is {name}={default})" if default else "")


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _int(env: Mapping[str, str], name: str, minimum: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return int(DEFAULTS[name])
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"{name} must be a whole number, got {raw!r}", hint=_fix_hint(name)
        ) from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}", hint=_fix_hint(name))
    return value


def _bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    value = raw.lower().strip("\"'")
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"{name} must be true or false, got {raw!r}", hint=f"Set {name}=true or {name}=false"
    )


def _tolerance(env: Mapping[str, str]) -> float:
    raw = _get(env, "DRILL_TOLERANCE")
    if raw is None:
        return float(DEFAULTS["DRILL_TOLERANCE"])
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(
            f"DRILL_TOLERANCE must be a number like 0.01, got {raw!r}",
            hint=_fix_hint("DRILL_TOLERANCE"),
        ) from None
    if not 0 <= value < 1:
        raise ConfigError(
            f"DRILL_TOLERANCE must be between 0 and 1 (0.01 = 1%), got {value}",
            hint=_fix_hint("DRILL_TOLERANCE"),
        )
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(default=None, repr=False)
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint: str | None = None
    s3_region: str = DEFAULTS["S3_REGION"]
    age_recipients: tuple[str, ...] = ()
    age_identity: str | None = field(default=None, repr=False)
    age_identity_file: str | None = None
    drill_database_url: str | None = field(default=None, repr=False)
    restore_database_url: str | None = field(default=None, repr=False)
    retention_days: int = int(DEFAULTS["RETENTION_DAYS"])
    min_keep: int = int(DEFAULTS["MIN_KEEP"])
    backup_at: str = DEFAULTS["BACKUP_AT"]
    heartbeat_url: str | None = field(default=None, repr=False)
    heartbeat_fail_url: str | None = field(default=None, repr=False)
    drill_tolerance: float = float(DEFAULTS["DRILL_TOLERANCE"])
    # Bucket-managed retention (TIERS). Empty means tool-managed: one folder, pruned by age.
    tiers: tuple[Tier, ...] = ()
    # Delete old backups after each backup. Defaults to on without TIERS and off with it.
    prune: bool = True

    @property
    def base_tier(self) -> Tier | None:
        """The most frequent tier, which gets the upload."""
        return self.tiers[0] if self.tiers else None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, require_bucket: bool = True
    ) -> Settings:
        env = os.environ if env is None else env

        bucket = _get(env, "S3_BUCKET")
        if require_bucket:
            check_bucket(bucket, present="S3_BUCKET" in env)

        identity = _get(env, "AGE_IDENTITY")
        identity_file = _get(env, "AGE_IDENTITY_FILE")
        if identity and identity_file:
            raise ConfigError(
                "both AGE_IDENTITY and AGE_IDENTITY_FILE are set",
                hint="Keep one: AGE_IDENTITY for the key itself, or AGE_IDENTITY_FILE for a file",
            )

        recipients = tuple(
            r.strip() for r in (_get(env, "AGE_RECIPIENTS") or "").split(",") if r.strip()
        )

        tiers = parse_tiers(_get(env, "TIERS"))

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
            s3_region=_get(env, "S3_REGION") or DEFAULTS["S3_REGION"],
            age_recipients=recipients,
            age_identity=identity,
            age_identity_file=identity_file,
            drill_database_url=drill_url,
            restore_database_url=restore_url,
            retention_days=_int(env, "RETENTION_DAYS", minimum=0),
            min_keep=_int(env, "MIN_KEEP", minimum=1),
            backup_at=_get(env, "BACKUP_AT") or DEFAULTS["BACKUP_AT"],
            heartbeat_url=_get(env, "HEARTBEAT_URL"),
            heartbeat_fail_url=_get(env, "HEARTBEAT_FAIL_URL"),
            drill_tolerance=_tolerance(env),
            tiers=tiers,
            prune=_bool(env, "PRUNE", default=not tiers),
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
        return check_bucket(self.s3_bucket, present=False)


def check_bucket(bucket: str | None, *, present: bool) -> str:
    """The bucket name, checked before any S3 call. `present`: S3_BUCKET exists but is blank."""
    if not bucket:
        if present:
            raise ConfigError(
                "S3_BUCKET is empty",
                hint="Set S3_BUCKET to the bucket's name, e.g. S3_BUCKET=my-backups",
            )
        raise ConfigError(
            "S3_BUCKET is not set",
            hint="Set S3_BUCKET to the bucket's name, e.g. S3_BUCKET=my-backups",
        )
    if "/" in bucket:
        name, _, folder = bucket.partition("/")
        raise ConfigError(
            f"S3_BUCKET has a folder in it ({bucket})",
            hint=f"S3_BUCKET is just the name: S3_BUCKET={name}, and the folder goes in "
            f"S3_PREFIX={folder.strip('/')}",
        )
    return bucket


def endpoint_path(endpoint: str | None) -> str | None:
    """The path in S3_ENDPOINT, if it has one (https://x.r2.cloudflarestorage.com/backups ->
    /backups). An endpoint is just the scheme and host: the bucket goes in S3_BUCKET."""
    if not endpoint:
        return None
    path = urlsplit(endpoint).path
    return path if path.strip("/") else None


def endpoint_base(endpoint: str) -> str:
    """S3_ENDPOINT without its path."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else endpoint
