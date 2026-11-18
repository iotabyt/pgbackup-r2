"""Times of day for the built-in scheduler. No cron daemon needed."""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .errors import ConfigError

MAX_SLEEP = 300.0  # seconds; wake up at least every 5 minutes


def parse_times(spec: str) -> list[tuple[int, int]]:
    """'02:00,14:30' -> [(2, 0), (14, 30)], sorted and de-duplicated. UTC."""
    times: set[tuple[int, int]] = set()
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        hour_s, sep, minute_s = item.partition(":")
        ok = (
            sep == ":"
            and hour_s.isdigit()
            and minute_s.isdigit()
            and len(minute_s) == 2
            and 1 <= len(hour_s) <= 2
        )
        if ok:
            hour, minute = int(hour_s), int(minute_s)
            ok = 0 <= hour <= 23 and 0 <= minute <= 59
        if not ok:
            raise ConfigError(f"{item!r} isn't a valid time: use HH:MM in UTC, e.g. 02:00 or 14:30")
        times.add((hour, minute))
    if not times:
        raise ConfigError("no backup times given: use HH:MM in UTC, e.g. 02:00")
    return sorted(times)


def format_times(times: list[tuple[int, int]]) -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m in times)


def next_run(now: datetime, times: list[tuple[int, int]]) -> datetime:
    """The first scheduled time strictly after `now` (UTC)."""
    now = now.astimezone(UTC)
    for hour, minute in sorted(times):
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    hour, minute = min(times)
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)


def sleep_until(
    target: datetime,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = _time.sleep,
) -> None:
    """Sleep in chunks of at most 5 minutes, so clock jumps and suspends don't drift us."""
    while (remaining := (target - now()).total_seconds()) > 0:
        sleep(min(remaining, MAX_SLEEP))
