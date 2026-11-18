"""Talking to Postgres through psql, pg_dump and pg_restore.

Connection details always travel in the child's environment (see pgurl.child_env),
never on its command line.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Sequence

from .agecrypt import recipient_args
from .errors import CommandError
from .pgurl import child_env, parse_url
from .util import run, stderr_tail

PGDMP_MAGIC = b"PGDMP"

# Builds one `SELECT ... UNION ALL SELECT ...` of exact count(*) queries, one per user table.
# Keys are schema.table via quote_ident, so "Readings" stays public."Readings".
COUNT_QUERY_SQL = """
SELECT string_agg(
         format('SELECT %L::text AS t, count(*)::bigint AS n FROM %I.%I',
                quote_ident(s.schemaname) || '.' || quote_ident(s.relname),
                s.schemaname, s.relname),
         ' UNION ALL '
         ORDER BY s.schemaname, s.relname)
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE c.relispopulated
"""


def psql(url: str, sql: str) -> str:
    args = ["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql]
    return run(args, env=child_env(url), what="psql").stdout.decode().strip()


def server_version(url: str) -> str:
    return psql(url, "SHOW server_version")


def table_counts(url: str) -> dict[str, int]:
    """Exact row count of every user table, keyed schema.table."""
    union = psql(url, COUNT_QUERY_SQL)
    if not union:
        return {}
    result = psql(url, f"SELECT coalesce(json_object_agg(t, n), '{{}}') FROM ({union}) q")
    return {k: int(v) for k, v in sorted(json.loads(result).items())}


def dump_encrypted(url: str, recipients: Sequence[str], out_path: str) -> None:
    """pg_dump -Fc piped straight into age: the plaintext dump never touches disk."""
    age_args = ["age", *recipient_args(recipients), "-o", out_path]  # validates first
    env = child_env(url)
    with tempfile.TemporaryFile() as dump_err, tempfile.TemporaryFile() as age_err:
        dump = subprocess.Popen(
            ["pg_dump", "-Fc", "--no-owner", "--no-acl"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=dump_err,
        )
        try:
            age = subprocess.Popen(age_args, stdin=dump.stdout, stderr=age_err)
        except BaseException:
            dump.kill()
            dump.wait()
            raise
        finally:
            assert dump.stdout is not None
            dump.stdout.close()  # age owns the read end now; pg_dump gets SIGPIPE if age dies
        age_rc = age.wait()
        dump_rc = dump.wait()

        def err(f) -> str:
            f.seek(0)
            return stderr_tail(f.read())

        if dump_rc != 0:
            raise CommandError(f"pg_dump failed (exit {dump_rc}): {err(dump_err)}")
        if age_rc != 0:
            raise CommandError(f"age failed while encrypting (exit {age_rc}): {err(age_err)}")


def restore(url: str, dump_path: str) -> None:
    dbname = parse_url(url).dbname
    args = [
        "pg_restore",
        "--no-owner",
        "--no-acl",
        "--clean",
        "--if-exists",
        "--exit-on-error",
        "-d",
        dbname,  # just the name; host, user and password come from the environment
        dump_path,
    ]
    _with_version_hint(lambda: run(args, env=child_env(url), what="pg_restore"))


def list_toc(dump_path: str) -> str:
    """pg_restore --list: reads the archive's table of contents, no database involved."""
    proc = _with_version_hint(
        lambda: run(["pg_restore", "--list", dump_path], what="pg_restore --list")
    )
    return proc.stdout.decode()


def _with_version_hint(fn):
    """Explain the two classic client/server version mismatches in plain words."""
    try:
        return fn()
    except CommandError as exc:
        text = str(exc)
        if "transaction_timeout" in text:
            hint = (
                "This pg_restore is newer than the target server: tools from Postgres 17+ "
                "send `SET transaction_timeout`, which older servers reject"
            )
        elif "unsupported version" in text and "file header" in text:
            hint = "The dump was made by a newer pg_dump than this pg_restore can read"
        else:
            raise
        raise CommandError(
            f"{text}. {hint}. Use the image tag that matches your Postgres major version "
            "(e.g. pg16) for both backups and restores"
        ) from None


def is_pg_dump(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(len(PGDMP_MAGIC)) == PGDMP_MAGIC
