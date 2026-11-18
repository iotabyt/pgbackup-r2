"""One set of defaults, shown the same everywhere: code, pgb init's template, docs, --help."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from pgbackup_r2.config import DEFAULTS, KNOWN_SETTINGS, Settings

ROOT = Path(__file__).resolve().parent.parent
PGB = (ROOT / "scripts" / "pgb").read_text()
PGB_PS1 = (ROOT / "scripts" / "pgb.ps1").read_text()
TEMPLATE = re.search(r"cat >\.env <<'ENV'\n(.*?\n)ENV\n", PGB, re.S).group(1)
ENV_DOC = (ROOT / "docs/src/content/docs/reference/environment.md").read_text()


def test_the_defaults_are_the_ones_we_promise():
    assert DEFAULTS == {
        "PG_MAJOR": "16",
        "S3_REGION": "auto",
        "BACKUP_AT": "02:00",
        "RETENTION_DAYS": "30",
        "MIN_KEEP": "7",
        "DRILL_TOLERANCE": "0.01",
    }


def test_code_uses_them():
    s = Settings.from_env({}, require_bucket=False)
    assert s.s3_region == DEFAULTS["S3_REGION"]
    assert s.backup_at == DEFAULTS["BACKUP_AT"]
    assert s.retention_days == int(DEFAULTS["RETENTION_DAYS"])
    assert s.min_keep == int(DEFAULTS["MIN_KEEP"])
    assert s.drill_tolerance == float(DEFAULTS["DRILL_TOLERANCE"])
    assert Settings() == s  # the dataclass's own defaults agree too


# Kept out of the template on purpose: rarely changed, documented in the reference only.
NOT_IN_TEMPLATE = {"DRILL_TOLERANCE"}


@pytest.mark.parametrize("name", sorted(set(DEFAULTS) - NOT_IN_TEMPLATE))
def test_init_template_shows_them(name):
    want = f"{name}={DEFAULTS[name]}"
    if name == "PG_MAJOR":
        assert f"\n{want}\n" in TEMPLATE  # needed to back up: filled in
    else:
        assert f"\n# {want}\n" in TEMPLATE  # optional: commented out, at its default


@pytest.mark.parametrize("name", sorted(NOT_IN_TEMPLATE))
def test_reference_only_settings_stay_out_of_the_template(name):
    assert f"{name}=" not in TEMPLATE
    assert f"| `{name}` |" in ENV_DOC


def test_removed_settings_are_gone():
    assert "PGB_DOCKER_ARGS" not in KNOWN_SETTINGS
    for text in (PGB, PGB_PS1, ENV_DOC):
        assert "PGB_DOCKER_ARGS" not in text
    compose = (ROOT / "examples/drill/compose.yml").read_text()
    assert "PGBACKUP_IMAGE" not in compose and "${PGB_IMAGE:-" in compose


@pytest.mark.parametrize("name", sorted(DEFAULTS))
def test_docs_reference_shows_them(name):
    row = re.search(rf"^\| `{name}` \| `([^`]*)` \|", ENV_DOC, re.M)
    assert row, f"{name} has no row with a default in reference/environment.md"
    assert row.group(1) == DEFAULTS[name]


def test_pg_major_default_everywhere():
    pg = DEFAULTS["PG_MAJOR"]
    assert f"pg=${{pg:-{pg}}}" in PGB
    assert f"if (-not $pg) {{ $pg = '{pg}' }}" in PGB_PS1
    assert f"ARG PG_MAJOR={pg}\n" in (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "examples/drill/compose.yml").read_text()
    assert compose.count(f"${{PG_MAJOR:-{pg}}}") == 2
    assert f"\nPG_MAJOR={pg}\n" in (ROOT / "examples/drill/.env.example").read_text()


def test_help_shows_them():
    def help_for(*args: str) -> str:
        return subprocess.run(
            [sys.executable, "-m", "pgbackup_r2.cli", *args, "--help"],
            capture_output=True,
            text=True,
            env={"NO_COLOR": "1", "COLUMNS": "200", "PATH": "/usr/bin:/bin"},
            check=True,
        ).stdout

    schedule = help_for("schedule")
    assert f"BACKUP_AT ({DEFAULTS['BACKUP_AT']} UTC)" in schedule
    assert f"(default: $BACKUP_AT or {DEFAULTS['BACKUP_AT']})" in schedule
    prune = help_for("prune")
    assert f"RETENTION_DAYS ({DEFAULTS['RETENTION_DAYS']})" in prune
    assert f"MIN_KEEP ({DEFAULTS['MIN_KEEP']})" in prune


def test_every_setting_in_the_template_and_docs_is_known():
    in_template = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", TEMPLATE, re.M))
    in_docs = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", ENV_DOC, re.M))
    assert in_template <= set(KNOWN_SETTINGS)
    assert in_docs <= set(KNOWN_SETTINGS)


def test_every_setting_the_code_reads_is_known():
    src = "".join(p.read_text() for p in (ROOT / "src/pgbackup_r2").glob("*.py"))
    read = set(re.findall(r'_(?:get|int|bool)\(env, "([A-Z0-9_]+)"', src))
    read |= set(re.findall(r'os\.environ\.get\("([A-Z0-9_]+)"', src))
    assert read and read <= set(KNOWN_SETTINGS), read - set(KNOWN_SETTINGS)
