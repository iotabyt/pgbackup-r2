"""Which backups to prune. Pure, so it's easy to test."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .stamps import parse_stamp


def select_expired(
    stamps: Iterable[str], now: datetime, retention_days: int, min_keep: int
) -> list[str]:
    """Stamps older than `retention_days`, never touching the newest `min_keep`.

    Returned oldest first.
    """
    newest_first = sorted(set(stamps), reverse=True)
    cutoff = now - timedelta(days=retention_days)
    candidates = newest_first[max(min_keep, 0) :]
    return sorted(s for s in candidates if parse_stamp(s) < cutoff)
