"""Pure checks: comparing row counts, reading pg_restore --list output."""

from __future__ import annotations

import re
from collections.abc import Mapping

_PLAIN_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")
_TOC_LINE = re.compile(r"^\d+; \d+ \d+ (.*)$")
_DUMPED_FROM = re.compile(r"^;\s+Dumped from database version:\s*(.+)$", re.MULTILINE)


def allowed_drift(expected: int, tolerance: float) -> int:
    """Exact match for empty tables, otherwise max(1, expected * tolerance)."""
    if expected == 0:
        return 0
    return max(1, int(expected * tolerance))


def compare_counts(
    expected: Mapping[str, int], actual: Mapping[str, int], tolerance: float
) -> list[str]:
    """One human-readable line per problem; empty means everything matches."""
    problems: list[str] = []
    for table in sorted(expected.keys() - actual.keys()):
        problems.append(f"missing table {table} (manifest has {expected[table]:,} rows)")
    for table in sorted(actual.keys() - expected.keys()):
        problems.append(
            f"extra table {table} ({actual[table]:,} rows) isn't in the manifest; "
            "the scratch database should start empty"
        )
    for table in sorted(expected.keys() & actual.keys()):
        want, got = expected[table], actual[table]
        allowed = allowed_drift(want, tolerance)
        if abs(got - want) > allowed:
            problems.append(
                f"{table}: expected {want:,} rows, restored {got:,} "
                f"(off by {abs(got - want):,}, allowed {allowed:,})"
            )
    return problems


def count_report(
    expected: Mapping[str, int], actual: Mapping[str, int], tolerance: float
) -> list[tuple[str, int | None, int | None, bool]]:
    """(table, expected, restored, ok) for every table on either side, sorted by name."""
    rows = []
    for table in sorted(expected.keys() | actual.keys()):
        want, got = expected.get(table), actual.get(table)
        ok = (
            want is not None
            and got is not None
            and abs(got - want) <= allowed_drift(want, tolerance)
        )
        rows.append((table, want, got, ok))
    return rows


def quote_ident(name: str) -> str:
    """Close to Postgres quote_ident (keywords aside, which we only use for display)."""
    if _PLAIN_IDENT.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def split_qualified(key: str) -> tuple[str, str]:
    """'public."Readings"' -> ('public', 'Readings'). Inverse of quote_ident(s).quote_ident(t)."""
    parts: list[str] = []
    i, n = 0, len(key)
    while i <= n:
        if i < n and key[i] == '"':
            i += 1
            buf = []
            while i < n:
                if key[i] == '"':
                    if i + 1 < n and key[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(key[i])
                i += 1
            parts.append("".join(buf))
        else:
            end = key.find(".", i)
            end = n if end == -1 else end
            parts.append(key[i:end])
            i = end
        if i < n and key[i] == ".":
            i += 1
            continue
        break
    if len(parts) != 2:
        return "", key
    return parts[0], parts[1]


def toc_tables(toc: str) -> list[tuple[str, str]]:
    """(schema, table) for every TABLE entry in pg_restore --list output."""
    tables = []
    for line in toc.splitlines():
        m = _TOC_LINE.match(line.strip())
        if not m:
            continue
        rest = m.group(1)
        if not rest.startswith("TABLE ") or rest.startswith("TABLE DATA "):
            continue
        body = rest[len("TABLE ") :]
        schema, _, remainder = body.partition(" ")
        name = remainder.rsplit(" ", 1)[0] if " " in remainder else remainder
        tables.append((schema, name))
    return sorted(tables)


def dumped_from(toc: str) -> str | None:
    m = _DUMPED_FROM.search(toc)
    return m.group(1).strip() if m else None
