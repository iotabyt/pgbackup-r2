"""Heartbeat pings (healthchecks.io, Better Stack, Uptime Kuma, ...).

A failed ping is logged and ignored: it must never fail the backup itself.
"""

from __future__ import annotations

import logging
import urllib.request
from urllib.parse import urlsplit

from . import __version__

log = logging.getLogger(__name__)

TIMEOUT = 10
MAX_BODY = 10_000


def ping(url: str | None, *, failure: str | None = None) -> None:
    """GET `url` on success; POST the error text when `failure` is given."""
    if not url:
        return
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        log.warning("heartbeat URL must start with http:// or https://, skipping the ping")
        return
    data = None if failure is None else failure.encode("utf-8", errors="replace")[:MAX_BODY]
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers={
            "User-Agent": f"pgbackup-r2/{__version__}",
            **({"Content-Type": "text/plain; charset=utf-8"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except Exception as exc:  # noqa: BLE001 - any failure here is only a warning
        log.warning("heartbeat ping to %s failed (continuing): %s", parts.hostname, exc)
