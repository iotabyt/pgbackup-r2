"""Bucket-managed retention: backups spread over tier folders (daily/, monthly/, ...).

TIERS="daily=daily/ monthly=monthly/" maps tier names to folders under S3_PREFIX. The most
frequent tier gets every backup as an upload. Every other tier gets a server-side copy of the
first backup of each of its periods. Which periods are covered is read from the bucket, never
from local state. Deleting old backups is left to the bucket's lifecycle rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .errors import ConfigError

# Most frequent first. The order decides which tier gets the upload.
TIER_NAMES = ("hourly", "daily", "weekly", "monthly", "yearly")

EXAMPLE = 'TIERS="daily=daily/ monthly=monthly/"'

# Words in a folder name that say how often it gets a backup, for `init --detect`.
_FOLDER_WORDS = {
    "hourly": ("hourly", "hour", "hours", "hourlies"),
    "daily": ("daily", "day", "days", "dailies", "nightly", "night", "nightlies"),
    "weekly": ("weekly", "week", "weeks", "weeklies"),
    "monthly": ("monthly", "month", "months", "monthlies"),
    "yearly": ("yearly", "year", "years", "annual", "annually", "yearlies"),
}


@dataclass(frozen=True)
class Tier:
    name: str  # one of TIER_NAMES
    folder: str  # relative to S3_PREFIX, without leading or trailing slashes

    @property
    def rank(self) -> int:
        return TIER_NAMES.index(self.name)


def parse_tiers(raw: str | None) -> tuple[Tier, ...]:
    """Parse TIERS. Returns the tiers most frequent first, or () when unset."""
    if raw is None:
        return ()
    value = raw.strip()
    # Docker's --env-file keeps quotes as part of the value; accept them either way.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        return ()

    def bad(problem: str) -> ConfigError:
        return ConfigError(
            f"TIERS: {problem}",
            hint=f"Write it as tier=folder pairs separated by spaces, e.g. {EXAMPLE}. "
            f"Tiers: {', '.join(TIER_NAMES)}",
        )

    tiers: list[Tier] = []
    for item in value.split():
        name, sep, folder = item.partition("=")
        name = name.strip().lower()
        folder = folder.strip().strip("/")
        if not sep or not name:
            raise bad(f"{item!r} isn't tier=folder")
        if name not in TIER_NAMES:
            raise bad(f"{name!r} isn't a tier name")
        if not folder:
            raise bad(f"{name} has no folder")
        if any(part in ("", ".", "..") for part in folder.split("/")):
            raise bad(f"{folder!r} isn't a usable folder name")
        if any(t.name == name for t in tiers):
            raise bad(f"{name} is listed twice")
        if any(t.folder == folder for t in tiers):
            raise bad(f"two tiers use the folder {folder}/")
        tiers.append(Tier(name, folder))
    return tuple(sorted(tiers, key=lambda t: t.rank))


def format_tiers(tiers: Iterable[Tier]) -> str:
    return 'TIERS="' + " ".join(f"{t.name}={t.folder}/" for t in tiers) + '"'


def period(tier: str, when: datetime) -> str:
    """The period a backup taken at `when` (UTC) belongs to, for this tier.

    hourly is every run, so each run is its own period. A daily tier that isn't the most
    frequent one keeps the first backup of each UTC day.
    """
    if tier == "hourly":
        return when.strftime("%Y-%m-%dT%H%M%S")
    if tier == "daily":
        return when.strftime("%Y-%m-%d")
    if tier == "weekly":
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    if tier == "monthly":
        return when.strftime("%Y-%m")
    if tier == "yearly":
        return when.strftime("%Y")
    raise ValueError(tier)


def guess_tier(folder: str) -> str | None:
    """The tier a folder's name suggests: backups/nightly -> daily. None if it says nothing."""
    words = re.split(r"[^a-z]+", folder.rstrip("/").rsplit("/", 1)[-1].lower())
    found = {name for name, vocab in _FOLDER_WORDS.items() for w in words if w in vocab}
    return found.pop() if len(found) == 1 else None


# --- lifecycle rules ------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    prefix: str
    days: int | None  # Expiration.Days, if the rule deletes by age


def lifecycle_rules(config: Mapping) -> list[Rule]:
    """Enabled rules from a GetBucketLifecycleConfiguration response."""
    rules = []
    for r in config.get("Rules", []):
        if r.get("Status", "Enabled") != "Enabled":
            continue
        f = r.get("Filter") or {}
        prefix = f.get("Prefix", (f.get("And") or {}).get("Prefix", r.get("Prefix", ""))) or ""
        days = (r.get("Expiration") or {}).get("Days")
        rules.append(Rule(prefix, int(days) if days is not None else None))
    return rules


def rule_for(key_prefix: str, rules: Sequence[Rule]) -> Rule | None:
    """The most specific rule that covers everything under `key_prefix`."""
    covering = [r for r in rules if key_prefix.startswith(r.prefix) and r.days is not None]
    return max(covering, key=lambda r: len(r.prefix), default=None)


# --- detection ------------------------------------------------------------------------------


@dataclass
class Folder:
    name: str  # relative to S3_PREFIX, e.g. "daily"
    backups: int = 0
    foreign: int = 0  # objects that aren't pgbackup-r2 backups or manifests
    tier: str | None = None
    expire_days: int | None = None


@dataclass
class Layout:
    folders: list[Folder] = field(default_factory=list)
    lifecycle_readable: bool = True
    suggestion: tuple[Tier, ...] = ()

    def describe(self) -> list[str]:
        """What was found, in sentences, for `init --detect` and `doctor`."""
        if not self.folders:
            return ["No folders under the prefix."]

        names = []
        for f in self.folders:
            if f.expire_days is None:
                names.append(f"{f.name}/")
            else:
                said = any("lifecycle:" in n for n in names)
                names.append(
                    f"{f.name}/ ({'' if said else 'lifecycle: '}delete after {f.expire_days} days)"
                )
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        lines = [f"Found folders {joined}."]
        if not self.lifecycle_readable:
            lines.append("Couldn't read the bucket's lifecycle rules (not allowed or not set).")
        for f in self.folders:
            if f.foreign:
                lines.append(
                    f"{f.foreign} other file{'s' if f.foreign != 1 else ''} in {f.name}/ "
                    "not made by pgbackup-r2 (left alone)"
                )
        unknown = [f.name + "/" for f in self.folders if f.tier is None]
        if unknown and self.suggestion:
            names = ", ".join(unknown)
            lines.append(f"Not used as tiers (the name doesn't say how often): {names}")
        return lines

    def suggested_lines(self) -> list[str]:
        if not self.suggestion:
            return []
        return [format_tiers(self.suggestion), "PRUNE=false"]


def suggest(folders: list[Folder]) -> tuple[Tier, ...]:
    """One folder per tier, picked by name. If two folders claim a tier, prefer the one that
    already has pgbackup-r2 backups, then the shorter name."""
    chosen: dict[str, Folder] = {}
    for f in folders:
        if f.tier is None:
            continue
        current = chosen.get(f.tier)
        if current is None or (f.backups, -len(f.name)) > (current.backups, -len(current.name)):
            chosen[f.tier] = f
    return tuple(sorted((Tier(n, f.name) for n, f in chosen.items()), key=lambda t: t.rank))
