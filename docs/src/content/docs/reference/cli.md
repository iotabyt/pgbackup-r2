---
title: CLI commands
description: The commands inside the image, pgbackup-r2 backup, drill, restore and the rest.
---

These are the commands of the `pgbackup-r2` program inside the image. `pgb <command>` runs them for you. Without `pgb`, run `docker run … ghcr.io/iotabyt/pgbackup-r2:pg16 <command>`, or `pgbackup-r2 <command>` if you [installed it without Docker](/start/install/#without-docker).

Settings come from [environment variables](/reference/environment/).

**Global flags**, accepted by every command:

- `--plain`: no colour, spinners or tables
- `-q`, `--quiet`: warnings only
- `--version`
- `-h`, `--help`

A **`STAMP`** can be:

- `latest` (the default), the newest backup in any tier
- a stamp like `2026-11-18T020000Z`
- `<tier>/<stamp>`, with `TIERS`
- a full key

## Help

`--help` on any command shows a one-line summary, the usage, and examples:

```text
$ pgb drill --help
Restore a backup into a throwaway database and check every table.

Usage: pgb drill [STAMP]
...
```

Through `pgb`, usage and examples say `pgb`. Run directly, they say `pgbackup-r2`.

## Output

A database is shown as `host/dbname`, with `:port` if it isn't 5432: `ep-withered-cell-batk9adw.c-8.eu-central-1.aws.neon.tech/labpadi`. The user, password and query string (`sslmode`, `channel_binding`) never appear. `restore` names its target once, in its final `PASS` line.


Output is **interactive**, with spinners, ✓ steps, tables and a big PASS/FAIL, when stdout is a terminal and `NO_COLOR` isn't set. Otherwise it's **plain**: timestamped log lines on stderr, and results on stdout. `--plain` forces plain output, and `schedule` is always plain. Docker only gives a container a terminal with `-it`, which `pgb` and `docker compose run` add for you.

## `backup`

`backup [--no-prune]`

1. Counts every table's rows.
2. Dumps, encrypts and uploads the dump.
3. Uploads the encrypted manifest.
4. Copies the backup to other tiers (with `TIERS`).
5. Prunes (if `PRUNE` is on, and not `--no-prune`).
6. Pings `HEARTBEAT_URL`.

It needs `DATABASE_URL`, `AGE_RECIPIENTS` and the bucket settings. On failure, it POSTs to `HEARTBEAT_FAIL_URL`.

## `list`

Every backup, oldest first: stamp, size and key, plus age in a terminal. With `TIERS`, it adds a tier column. It ends with the last drill's result, such as `Last drill: PASS, 12 days ago`. Files that aren't pgbackup-r2 backups are ignored.

## `prune`

`prune [--dry-run]`

Deletes dumps and manifests older than `RETENTION_DAYS`, always keeping the newest `MIN_KEEP`. With `TIERS`, only the most frequent tier is pruned. Deletes refused by a lock are skipped with a warning. `--dry-run` shows what would go. See [Retention](/concepts/retention/).

## `verify`

`verify [STAMP] [--file PATH]`

Downloads a backup and checks its sha256. Then it decrypts it, checks it's a `pg_dump` archive, and lists its tables with the manifest's row counts. No database is needed. `--file` checks a `.dump.age` on disk instead, with no S3 settings. A `.manifest.age` next to it is used if it's there.

## `drill`

`drill [STAMP] [--preflight]`

Restores a backup into `DRILL_DATABASE_URL` and compares every table's row count with the manifest. It records the result in `<prefix>/.drills/`, then pings the heartbeat. Exits 1 on FAIL. It refuses to run if `DRILL_DATABASE_URL` is the same database as `DATABASE_URL`. `pgb drill` provides the scratch database for you.

`--preflight` runs only the cheap checks: a private key is set, the bucket answers, and the backup exists. It restores nothing and needs no `DRILL_DATABASE_URL`. `pgb drill` runs it before starting Postgres.

## `restore`

`restore [STAMP] [--target URL] [--to-production]`

1. Checks the checksum.
2. Decrypts the backup.
3. Runs `pg_restore --clean --if-exists` into `RESTORE_DATABASE_URL`, or into `--target`, which takes precedence.

It refuses the live database (`DATABASE_URL`) unless `--to-production` is given. Even then, you have to type the database name at a terminal. See [Restoring on a bad day](/guides/restoring/).

## `decrypt`

`decrypt [STAMP] --out PATH [--force] [--file PATH]`

Writes a plain `pg_dump` custom-format file to `--out`. With `pgb`, that's a path under `/out`, such as `/out/backup.dump`. `--force` overwrites. `--file` decrypts a local `.dump.age` with no S3 settings.

## `keygen`

Prints a new age key pair: the public key for `AGE_RECIPIENTS`, and the private key for `AGE_IDENTITY`. Nothing is written to disk. `--plain` prints them exactly as they go in `.env`.

## `detect`

`detect [--env-lines]`

Lists the folders under the prefix, what they hold and their lifecycle rules, and suggests a `TIERS` line. With `--env-lines`, the explanation goes to stderr and only the suggested `.env` lines go to stdout. That's what `pgb init --detect` uses.

## `doctor`

Checks, with a ✓, ✗, ⚠ or `-` line each, and a fix under every problem:

- **Settings:** everything a backup needs is set.
- **Database:** reachable, with the same major version as `PG_MAJOR` and the image's tools.
- **Bucket:** writable. It puts and deletes a test file under the prefix.
- **Layout:** the tiers, lifecycle rules, other files, and a suggested `TIERS`.
- **Keys:** if `AGE_IDENTITY` is set, it opens what's encrypted to `AGE_RECIPIENTS`.
- **Drills:** when a drill last passed.

Exits 1 if any check is ✗. A ⚠ or `-` doesn't fail it. [Troubleshooting](/troubleshooting/#doctors-messages) explains each message.

## `schedule`

`schedule [--at HH:MM[,HH:MM]] [--run-now]`

Stays running and backs up at the given UTC times: `--at`, else `BACKUP_AT`, else 02:00. This is the image's default command. A failed backup is logged and reported to `HEARTBEAT_FAIL_URL`, and the schedule carries on. `--run-now` also backs up straight away. It stops cleanly on SIGTERM, such as from `docker stop`.
